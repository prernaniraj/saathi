import logging
import os
import re
import time
import uuid
from pathlib import Path
from datetime import date, datetime
from datetime import date as _date  # Alias: a model field is itself named `date` below.
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from . import aftercare, auth, breach, careplans, credentials, evv, incidents, matching, notifications, privacy, reports, service
from .domain import Ctx, DomainError
from .observability import event, metrics, otel_trace_id, request_id_var, setup_logging, setup_tracing, trace_id_var
from .store import Store
from .trace import implements

ROOT = Path(__file__).resolve().parent.parent
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
LABELS = {"dateOfBirth": "Date of birth", "status": "Status", "visitsPerWeek": "Visits per week", "requestId": "Family", "caregiverId": "Caregiver", "name": "Name", "city": "City", "family": "Family name", "parent": "Parent’s name", "note": "Note", "mood": "Mood", "need": "Need", "email": "Email", "password": "Password", "role": "Role", "institution": "Institution", "aftercareUntil": "Aftercare until", "contacts": "Contacts", "type": "Type", "reference": "Reference", "details": "Details", "language": "Language", "plannedAt": "Planned time", "timestamp": "Timestamp", "location": "Location", "goals": "Goals", "title": "Title", "languages": "Languages", "availability": "Availability", "capacityVisitsPerWeek": "Capacity", "issuedDate": "Issued date", "expiryDate": "Expiry date", "phone": "Phone", "days": "Days", "hours": "Hours", "severity": "Severity", "description": "Description", "owner": "Owner", "resolution": "Resolution", "personType": "Person type", "personId": "Person", "purposes": "Purposes", "affectedPersonIds": "Affected people", "discoveredAt": "Discovered at", "visitId": "Visit"}


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
    language: str | None = None


class MatchIn(BaseModel):
    caregiverId: str


class VisitIn(BaseModel):
    requestId: str
    mood: Literal["Good", "Okay", "Needs attention"]
    note: Text
    plannedAt: datetime | None = None


class ContactIn(BaseModel):
    name: Text
    role: Text
    phone: Text


class AftercareIn(BaseModel):
    institution: str | None = None
    aftercareUntil: date | None = None
    extended: bool | None = None
    contacts: list[ContactIn] | None = None
    locationConsent: bool | None = None


class DocumentIn(BaseModel):
    type: Text
    reference: Text
    status: Literal["Pending", "Verified", "Rejected"] = "Pending"


class HousingIn(BaseModel):
    status: Literal["Stable", "Temporary", "At risk", "Unknown"]
    details: str | None = None


class CheckinNoteIn(BaseModel):
    note: Text
    date: _date | None = None


class CredentialIn(BaseModel):
    type: Text
    issuedDate: date | None = None
    expiryDate: date


class AvailabilityIn(BaseModel):
    days: list[str]
    hours: str


class ProfileIn(BaseModel):
    languages: list[str] | None = None
    availability: AvailabilityIn | None = None
    capacityVisitsPerWeek: int | None = Field(default=None, ge=0, le=21)


class CheckInOut(BaseModel):
    timestamp: datetime | None = None
    location: str | None = None


class CarePlanIn(BaseModel):
    goals: LongText


class TaskIn(BaseModel):
    title: Text


class IncidentIn(BaseModel):
    requestId: str
    visitId: str | None = None
    severity: Literal["Low", "Medium", "High", "Critical"]
    description: Text


class AssignIn(BaseModel):
    owner: Text


class ResolveIn(BaseModel):
    resolution: Text


class ConsentIn(BaseModel):
    personType: Literal["caregiver", "parent"]
    personId: Text
    purposes: list[str]


class BreachIn(BaseModel):
    title: Text
    description: Text
    affectedPersonIds: list[str] = []
    discoveredAt: str | None = None


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
        result = service.create_request(store, ctx, body.family, body.parent, body.city, body.visitsPerWeek, body.need, language=(body.language or "").strip() or None)
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

    def _escalate_visit(store, ctx: Ctx, visit: dict) -> None:
        """Open the incident and send the notifications for a visit flagged
        "Needs attention" (REQ-SAFE-02, REQ-NOTIF-01). This runs after the
        visit itself is committed, and a failure here must never fail the
        visit: problems are logged with the request ID instead."""
        for fn in (incidents.open_for_visit, notifications.notify_for_visit):
            try:
                fn(store, ctx, visit)
            except DomainError as e:
                event(logging.WARNING, "visit escalation skipped", reason=e.message, visit_id=visit.get("id"))
            except Exception:
                logging.getLogger("saathi").exception("visit escalation failed")

    @app.post("/api/visits", status_code=201)
    def log_visit(body: VisitIn, ctx: C, user: U):
        if user["role"] == "family":
            raise DomainError(403, "Your role cannot do this.")
        if user["role"] == "caregiver":
            with store.lock:
                req = next((r for r in store.db["requests"] if r["id"] == body.requestId), None)
            if req is not None and req.get("caregiverId") != user.get("caregiverId"):
                raise DomainError(403, "Visits can only be logged for your own matched requests.")
        result = service.log_visit(store, ctx, body.requestId, body.mood, body.note, planned_at=body.plannedAt.isoformat() if body.plannedAt else None)
        if body.mood == "Needs attention":
            _escalate_visit(store, ctx, result)
        return result

    # --- Aftercare (REQ-AFT-01, REQ-AFT-03) ---
    @app.post("/api/caregivers/{caregiver_id}/aftercare")
    @implements("REQ-AFT-01")
    def set_aftercare(caregiver_id: str, body: AftercareIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return aftercare.set_aftercare(store, ctx, caregiver_id, institution=body.institution, aftercare_until=body.aftercareUntil, extended=body.extended, contacts=[c.model_dump() for c in body.contacts] if body.contacts is not None else None, location_consent=body.locationConsent)

    @app.post("/api/caregivers/{caregiver_id}/documents", status_code=201)
    @implements("REQ-AFT-03")
    def add_document(caregiver_id: str, body: DocumentIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return aftercare.add_document(store, ctx, caregiver_id, body.type, body.reference, body.status)

    @app.post("/api/caregivers/{caregiver_id}/housing")
    @implements("REQ-AFT-03")
    def set_housing(caregiver_id: str, body: HousingIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return aftercare.set_housing(store, ctx, caregiver_id, body.status, body.details)

    @app.post("/api/caregivers/{caregiver_id}/checkins", status_code=201)
    @implements("REQ-AFT-03")
    def add_checkin(caregiver_id: str, body: CheckinNoteIn, ctx: C, user: U):
        if user["role"] == "caregiver" and user.get("caregiverId") != caregiver_id:
            raise DomainError(403, "Check-ins can only be added for your own record.")
        require_role(user, "coordinator", "caregiver")
        return aftercare.add_checkin(store, ctx, caregiver_id, body.note, body.date)

    # --- Credentials (REQ-CRED-01) ---
    @app.post("/api/caregivers/{caregiver_id}/credentials", status_code=201)
    @implements("REQ-CRED-01")
    def add_credential(caregiver_id: str, body: CredentialIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return credentials.add_credential(store, ctx, caregiver_id, body.type, body.expiryDate, body.issuedDate)

    @app.get("/api/credentials/expiring")
    @implements("REQ-CRED-01")
    def expiring_credentials(user: U, days: int = Query(30, ge=0, le=365)):
        require_role(user, "coordinator")
        return {"days": days, "expiring": credentials.expiring_credentials(store, days)}

    # --- Matching profile and suggestions (REQ-MATCH-02) ---
    @app.post("/api/caregivers/{caregiver_id}/profile")
    @implements("REQ-MATCH-02")
    def set_profile(caregiver_id: str, body: ProfileIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return matching.set_profile(store, ctx, caregiver_id, languages=body.languages, availability=body.availability.model_dump() if body.availability else None, capacity_visits_per_week=body.capacityVisitsPerWeek)

    @app.get("/api/requests/{request_id}/suggestions")
    @implements("REQ-MATCH-02")
    def suggestions(request_id: str, user: U):
        require_role(user, "coordinator")
        return matching.suggest_caregivers(store, request_id)

    # --- Visit check-in and check-out (REQ-EVV-01) ---
    def _visit_or_404(visit_id: str) -> dict:
        with store.lock:
            visit = next((v for v in store.db["visits"] if v["id"] == visit_id), None)
        if visit is None:
            raise DomainError(404, "Visit not found.")
        return visit

    def _check_visit_owner(visit: dict, user: dict) -> None:
        if user["role"] == "family":
            raise DomainError(403, "Your role cannot do this.")
        if user["role"] == "caregiver" and visit.get("caregiverId") != user.get("caregiverId"):
            raise DomainError(403, "Check-in and check-out are only for your own visits.")

    @app.post("/api/visits/{visit_id}/checkin")
    @implements("REQ-EVV-01")
    def visit_checkin(visit_id: str, body: CheckInOut, ctx: C, user: U):
        visit = _visit_or_404(visit_id)
        _check_visit_owner(visit, user)
        return evv.check_in(store, ctx, visit_id, timestamp=body.timestamp.isoformat() if body.timestamp else None, location=body.location)

    @app.post("/api/visits/{visit_id}/checkout")
    @implements("REQ-EVV-01")
    def visit_checkout(visit_id: str, body: CheckInOut, ctx: C, user: U):
        visit = _visit_or_404(visit_id)
        _check_visit_owner(visit, user)
        return evv.check_out(store, ctx, visit_id, timestamp=body.timestamp.isoformat() if body.timestamp else None, location=body.location)

    # --- Care plans (REQ-PLAN-01) ---
    def _care_plan_scope(request_id: str, user: dict) -> dict:
        with store.lock:
            req = next((r for r in store.db["requests"] if r["id"] == request_id), None)
        if req is None:
            raise DomainError(404, "Request not found.")
        if user["role"] == "coordinator":
            return req
        if user["role"] == "caregiver" and req.get("caregiverId") == user.get("caregiverId"):
            return req
        if user["role"] == "family" and req["id"] == user.get("requestId"):
            return req
        raise DomainError(403, "Your role cannot do this.")

    @app.get("/api/requests/{request_id}/care-plan")
    @implements("REQ-PLAN-01")
    def get_care_plan(request_id: str, user: U):
        _care_plan_scope(request_id, user)
        plan = careplans.get_care_plan(store, request_id)
        if plan is None:
            raise DomainError(404, "Care plan not found.")
        return plan

    @app.post("/api/requests/{request_id}/care-plan")
    @implements("REQ-PLAN-01")
    def set_care_plan(request_id: str, body: CarePlanIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return careplans.set_care_plan(store, ctx, request_id, body.goals)

    @app.post("/api/requests/{request_id}/care-plan/tasks", status_code=201)
    @implements("REQ-PLAN-01")
    def add_care_plan_task(request_id: str, body: TaskIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return careplans.add_task(store, ctx, request_id, body.title)

    @app.post("/api/requests/{request_id}/care-plan/tasks/{task_id}/toggle")
    @implements("REQ-PLAN-01")
    def toggle_care_plan_task(request_id: str, task_id: str, ctx: C, user: U):
        if user["role"] == "caregiver":
            _care_plan_scope(request_id, user)  # Only the matched caregiver may toggle.
        else:
            require_role(user, "coordinator")
        return careplans.toggle_task(store, ctx, request_id, task_id)

    # --- Notifications (REQ-NOTIF-01) ---
    @app.get("/api/notifications")
    @implements("REQ-NOTIF-01")
    def list_notifications(user: U):
        return notifications.visible_notifications(store, user)

    @app.post("/api/notifications/{notification_id}/read")
    @implements("REQ-NOTIF-01")
    def read_notification(notification_id: str, ctx: C, user: U):
        return notifications.mark_read(store, ctx, notification_id, user)

    @app.post("/api/notifications/check-missed")
    @implements("REQ-NOTIF-01")
    def check_missed(ctx: C, user: U):
        require_role(user, "coordinator")
        return notifications.check_missed_visits(store, ctx)

    # --- Reports (REQ-REP-01) ---
    @app.get("/api/reports/summary")
    @implements("REQ-REP-01")
    def reports_summary(user: U):
        require_role(user, "coordinator")
        return reports.summary(store)

    # --- Incidents (REQ-SAFE-02) ---
    @app.get("/api/incidents")
    @implements("REQ-SAFE-02")
    def list_incidents(user: U):
        return incidents.list_incidents(store, user)

    @app.post("/api/incidents", status_code=201)
    @implements("REQ-SAFE-02")
    def create_incident(body: IncidentIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return incidents.create_incident(store, ctx, body.requestId, body.severity, body.description, visit_id=body.visitId, owner=user["email"])

    @app.post("/api/incidents/{incident_id}/assign")
    @implements("REQ-SAFE-02")
    def assign_incident(incident_id: str, body: AssignIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return incidents.assign_incident(store, ctx, incident_id, body.owner)

    @app.post("/api/incidents/{incident_id}/resolve")
    @implements("REQ-SAFE-02")
    def resolve_incident(incident_id: str, body: ResolveIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return incidents.resolve_incident(store, ctx, incident_id, body.resolution)

    # --- Consent and erasure (REQ-PRIV-01) ---
    @app.get("/api/consents")
    @implements("REQ-PRIV-01")
    def list_consents(user: U):
        return privacy.list_consents(store, user)

    @app.post("/api/consents", status_code=201)
    @implements("REQ-PRIV-01")
    def grant_consent(body: ConsentIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return privacy.grant_consent(store, ctx, body.personType, body.personId, body.purposes)

    @app.post("/api/consents/{consent_id}/withdraw")
    @implements("REQ-PRIV-01")
    def withdraw_consent(consent_id: str, ctx: C, user: U):
        require_role(user, "coordinator")
        return privacy.withdraw_consent(store, ctx, consent_id)

    @app.post("/api/caregivers/{caregiver_id}/erase")
    @implements("REQ-PRIV-01")
    def erase_caregiver(caregiver_id: str, ctx: C, user: U):
        require_role(user, "coordinator")
        return privacy.erase_caregiver(store, ctx, caregiver_id)

    # --- Breach register (REQ-PRIV-02) ---
    @app.get("/api/breaches")
    @implements("REQ-PRIV-02")
    def list_breaches(user: U):
        require_role(user, "coordinator")
        return breach.list_breaches(store)

    @app.post("/api/breaches", status_code=201)
    @implements("REQ-PRIV-02")
    def create_breach(body: BreachIn, ctx: C, user: U):
        require_role(user, "coordinator")
        return breach.create_breach(store, ctx, body.title, body.description, body.affectedPersonIds, body.discoveredAt)

    @app.post("/api/breaches/{breach_id}/notify")
    @implements("REQ-PRIV-02")
    def notify_breach(breach_id: str, ctx: C, user: U):
        require_role(user, "coordinator")
        return breach.notify_breach(store, ctx, breach_id)

    @app.post("/api/breaches/{breach_id}/report")
    @implements("REQ-PRIV-02")
    def report_breach(breach_id: str, ctx: C, user: U):
        require_role(user, "coordinator")
        return breach.report_breach(store, ctx, breach_id)

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
