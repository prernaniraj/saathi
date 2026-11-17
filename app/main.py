import logging
import os
import re
import time
import uuid
from pathlib import Path
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from . import auth, service
from .domain import Ctx, DomainError
from .observability import event, metrics, otel_trace_id, request_id_var, setup_logging, setup_tracing, trace_id_var
from .store import Store
from .trace import implements

ROOT = Path(__file__).resolve().parent.parent
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LABELS = {"dateOfBirth": "Date of birth", "status": "Status", "visitsPerWeek": "Visits per week", "requestId": "Family", "caregiverId": "Caregiver", "name": "Name", "city": "City", "family": "Family name", "parent": "Parent’s name", "note": "Note", "mood": "Mood", "need": "Need", "email": "Email", "password": "Password", "role": "Role"}


class CaregiverIn(BaseModel):
    name: Text
    city: Text
    dateOfBirth: date


class VerificationIn(BaseModel):
    status: Literal["Pending", "Verified", "Failed"]


class RequestIn(BaseModel):
    family: Text
    parent: Text
    city: Text
    visitsPerWeek: int = Field(ge=1, le=7)
    need: Text


class MatchIn(BaseModel):
    caregiverId: str


class VisitIn(BaseModel):
    requestId: str
    mood: Literal["Good", "Okay", "Needs attention"]
    note: Text


class LoginIn(BaseModel):
    email: Text
    password: str


class UserIn(BaseModel):
    email: Text
    password: str
    role: Literal["coordinator", "caregiver", "family"]
    caregiverId: str | None = None
    requestId: str | None = None


def _trace_id_from(header: str | None) -> str | None:
    """W3C traceparent: version-traceid-spanid-flags."""
    m = re.fullmatch(r"[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}", (header or "").strip())
    return m.group(1) if m and m.group(1) != "0" * 32 else None


def create_app(data_file: str | Path | None = None, public_dir: str | Path | None = ROOT / "public") -> FastAPI:
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    app = FastAPI(title="Saathi", version="0.4.0")
    app.state.store = store = Store(data_file)
    setup_tracing(app)

    @app.middleware("http")
    @implements("REQ-OBS-01", "REQ-OBS-02", "REQ-OBS-03")
    async def observe(request: Request, call_next):
        rid = re.sub(r"[^\w.-]", "", request.headers.get("x-request-id", ""))[:64] or uuid.uuid4().hex
        tid = _trace_id_from(request.headers.get("traceparent")) or otel_trace_id() or uuid.uuid4().hex
        request.state.request_id, request.state.trace_id = rid, tid
        t1, t2 = request_id_var.set(rid), trace_id_var.set(tid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logging.getLogger("saathi").exception("unhandled error")
            response = JSONResponse({"error": "Server error."}, status_code=500)
        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        path = route.path if route else "unmatched"
        metrics.inc("saathi_http_requests_total", method=request.method, route=path, status=response.status_code)
        metrics.observe("saathi_http_request_seconds", elapsed, route=path)
        actor = getattr(request.state, "user", None)
        actor = actor.get("email") if isinstance(actor, dict) else None
        event(logging.INFO, "request", method=request.method, route=path, status=response.status_code, duration_ms=round(elapsed * 1000, 2), actor=actor or request.headers.get("x-actor", "anonymous"))
        response.headers["X-Request-ID"], response.headers["X-Trace-ID"] = rid, tid
        request_id_var.reset(t1)
        trace_id_var.reset(t2)
        return response

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0]
        field = str(first["loc"][-1])
        label = LABELS.get(field, "Request")
        message = f"{label} is required." if first["type"] in ("missing", "string_too_short") else f"{label} is not valid: {first['msg']}."
        metrics.inc("saathi_validation_errors_total", field=field)
        return JSONResponse({"error": message}, status_code=400)

    @implements("REQ-RBAC-01")
    def require_user(request: Request) -> dict:
        """The authenticated user, from a bearer token.

        A valid token always wins and the X-Actor header is ignored. With no
        token the request is rejected with 401, except when
        SAATHI_DEV_NO_AUTH=1 (local-dev fallback only), where the X-Actor
        header is accepted as the coordinator, as before.
        """
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else None
        if token:
            user = auth.authenticate(store.db, token)
            if user is None:
                raise DomainError(401, "Login has expired or is not valid. Log in again.")
            request.state.user = user
            return user
        if os.environ.get("SAATHI_DEV_NO_AUTH") == "1":
            actor = request.headers.get("x-actor", "anonymous")[:64] or "anonymous"
            user = {"email": actor, "role": "coordinator", "caregiverId": None, "requestId": None}
            request.state.user = user
            return user
        raise DomainError(401, "Login required.")

    def require_role(user: dict, *roles: str) -> dict:
        if user["role"] not in roles:
            raise DomainError(403, "Your role cannot do this.")
        return user

    def ctx_of(request: Request, user: dict = Depends(require_user)) -> Ctx:
        # The audit actor is the authenticated user's email, never X-Actor.
        return Ctx(request.state.request_id, request.state.trace_id, user["email"][:64])

    C = Annotated[Ctx, Depends(ctx_of)]
    U = Annotated[dict, Depends(require_user)]

    @app.post("/api/auth/login")
    @implements("REQ-RBAC-01")
    def login(body: LoginIn):
        with store.lock:
            user = auth.find_user(store.db, body.email)
            if user is None or not auth.verify_password(body.password, user["password_salt"], user["password_hash"]):
                raise DomainError(401, "Email or password is not correct.")
            token = auth.create_session(store.db, user["email"])
            store.save()
        event(logging.INFO, "login", actor=user["email"])
        return {"token": token, "user": auth.public_user(user)}

    @app.post("/api/auth/logout")
    @implements("REQ-RBAC-01")
    def logout(request: Request, user: U):
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        with store.lock:
            auth.revoke_session(store.db, token)
            store.save()
        return {"ok": True}

    @app.get("/api/auth/me")
    @implements("REQ-RBAC-01")
    def me(user: U):
        return auth.public_user(user)

    @app.post("/api/auth/users", status_code=201)
    @implements("REQ-RBAC-01")
    def create_account(body: UserIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return service.create_user(store, ctx, body.email, body.role, body.password, body.caregiverId, body.requestId)

    @app.get("/api/state")
    def state(user: U):
        return service.get_state(store, user)

    @app.post("/api/caregivers", status_code=201)
    def add_caregiver(body: CaregiverIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return service.add_caregiver(store, ctx, body.name, body.city, body.dateOfBirth)

    @app.post("/api/caregivers/{caregiver_id}/start")
    def start(caregiver_id: str, ctx: C, user: U):
        require_role(user, "coordinator")
        return service.start_training(store, ctx, caregiver_id)

    @app.post("/api/caregivers/{caregiver_id}/certify")
    def certify(caregiver_id: str, ctx: C, user: U):
        require_role(user, "coordinator")
        return service.certify(store, ctx, caregiver_id)

    @app.post("/api/caregivers/{caregiver_id}/modules/{index}")
    def toggle(caregiver_id: str, index: int, ctx: C, user: U):
        require_role(user, "coordinator")
        return service.toggle_module(store, ctx, caregiver_id, index)

    @app.post("/api/caregivers/{caregiver_id}/verification/{item}")
    def verification(caregiver_id: str, item: str, body: VerificationIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return service.set_verification(store, ctx, caregiver_id, item, body.status)

    @app.post("/api/requests", status_code=201)
    def create_request(body: RequestIn, ctx: C, user: U):
        require_role(user, "coordinator", "family")
        result = service.create_request(store, ctx, body.family, body.parent, body.city, body.visitsPerWeek, body.need)
        if user["role"] == "family":
            # The request a family submits becomes the one linked to them.
            with store.lock:
                linked = auth.find_user(store.db, user["email"])
                if linked is not None:
                    linked["requestId"] = result["id"]
                    store.save()
        return result

    @app.post("/api/requests/{request_id}/match")
    def match(request_id: str, body: MatchIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return service.match(store, ctx, request_id, body.caregiverId)

    @app.post("/api/visits", status_code=201)
    def log_visit(body: VisitIn, ctx: C, user: U):
        if user["role"] == "family":
            raise DomainError(403, "Your role cannot do this.")
        if user["role"] == "caregiver":
            with store.lock:
                req = next((r for r in store.db["requests"] if r["id"] == body.requestId), None)
            if req is not None and req.get("caregiverId") != user.get("caregiverId"):
                raise DomainError(403, "Visits can only be logged for your own matched requests.")
        return service.log_visit(store, ctx, body.requestId, body.mood, body.note)

    @app.get("/api/audit")
    @implements("REQ-TRC-03", "REQ-RBAC-01")
    def audit(user: U, entity_id: str | None = None, action: str | None = None, limit: int = Query(50, ge=1, le=500)):
        return service.scoped_audit(store, user, entity_id, action, limit)

    @app.get("/api/audit/verify")
    @implements("REQ-TRC-02")
    def audit_verify(user: U):
        with store.lock:
            return store.audit.verify()

    @app.get("/healthz")
    @implements("REQ-OBS-04")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    @implements("REQ-OBS-04")
    def readyz():
        with store.lock:
            result = store.audit.verify()
        body = {"status": "ready" if result["ok"] else "audit chain broken", **result}
        return JSONResponse(body, status_code=200 if result["ok"] else 503)

    @app.get("/metrics", response_class=PlainTextResponse)
    @implements("REQ-OBS-03")
    def prometheus():
        return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")

    if public_dir and Path(public_dir).is_dir():
        app.mount("/", StaticFiles(directory=str(public_dir), html=True), name="public")
    return app


app = create_app(ROOT / "data" / "saathi.sqlite")
