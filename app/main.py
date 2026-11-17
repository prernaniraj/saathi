import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from . import service
from .domain import Ctx, DomainError
from .observability import event, metrics, otel_trace_id, request_id_var, setup_logging, setup_tracing, trace_id_var
from .store import Store
from .trace import implements

ROOT = Path(__file__).resolve().parent.parent
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LABELS = {"visitsPerWeek": "Visits per week", "requestId": "Family", "caregiverId": "Caregiver", "name": "Name", "city": "City", "family": "Family name", "parent": "Parent’s name", "note": "Note", "mood": "Mood", "need": "Need"}


class CaregiverIn(BaseModel):
    name: Text
    city: Text


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


def _trace_id_from(header: str | None) -> str | None:
    """W3C traceparent: version-traceid-spanid-flags."""
    m = re.fullmatch(r"[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}", (header or "").strip())
    return m.group(1) if m and m.group(1) != "0" * 32 else None


def create_app(data_file: str | Path | None = None, public_dir: str | Path | None = ROOT / "public") -> FastAPI:
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    app = FastAPI(title="Saathi", version="0.2.0")
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
        event(logging.INFO, "request", method=request.method, route=path, status=response.status_code, duration_ms=round(elapsed * 1000, 2), actor=request.headers.get("x-actor", "anonymous"))
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

    def ctx_of(request: Request) -> Ctx:
        # The actor is self-declared until authentication exists (REQ-RBAC-01).
        return Ctx(request.state.request_id, request.state.trace_id, request.headers.get("x-actor", "anonymous")[:64])

    C = Annotated[Ctx, Depends(ctx_of)]

    @app.get("/api/state")
    def state():
        return service.get_state(store)

    @app.post("/api/caregivers", status_code=201)
    def add_caregiver(body: CaregiverIn, ctx: C):
        return service.add_caregiver(store, ctx, body.name, body.city)

    @app.post("/api/caregivers/{caregiver_id}/start")
    def start(caregiver_id: str, ctx: C):
        return service.start_training(store, ctx, caregiver_id)

    @app.post("/api/caregivers/{caregiver_id}/certify")
    def certify(caregiver_id: str, ctx: C):
        return service.certify(store, ctx, caregiver_id)

    @app.post("/api/caregivers/{caregiver_id}/modules/{index}")
    def toggle(caregiver_id: str, index: int, ctx: C):
        return service.toggle_module(store, ctx, caregiver_id, index)

    @app.post("/api/requests", status_code=201)
    def create_request(body: RequestIn, ctx: C):
        return service.create_request(store, ctx, body.family, body.parent, body.city, body.visitsPerWeek, body.need)

    @app.post("/api/requests/{request_id}/match")
    def match(request_id: str, body: MatchIn, ctx: C):
        return service.match(store, ctx, request_id, body.caregiverId)

    @app.post("/api/visits", status_code=201)
    def log_visit(body: VisitIn, ctx: C):
        return service.log_visit(store, ctx, body.requestId, body.mood, body.note)

    @app.get("/api/audit")
    @implements("REQ-TRC-03")
    def audit(entity_id: str | None = None, action: str | None = None, limit: int = Query(50, ge=1, le=500)):
        with store.lock:
            return store.audit.query(entity_id, action, limit)

    @app.get("/api/audit/verify")
    @implements("REQ-TRC-02")
    def audit_verify():
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


app = create_app(ROOT / "data" / "db.json")
