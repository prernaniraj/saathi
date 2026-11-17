"""Incident reports and escalation. REQ-SAFE-02.

A "Needs attention" visit opens an incident; a coordinator assigns an owner
(a coordinator account) and resolves it with a resolution. Reads are
role-scoped: a coordinator sees all incidents, a caregiver sees incidents
about their own visits, a family sees incidents about its own request.

``open_for_visit`` is the hook the visit flow calls after logging a visit.
It is deliberately NOT wired into ``service.log_visit`` here; the merge wires
it in, so logging a visit never fails because of incident handling.
"""
import copy
from datetime import datetime, timezone

from .domain import Change, DomainError
from .service import mutation
from .trace import implements

SEVERITIES = ("Low", "Medium", "High", "Critical")
STATUSES = ("Open", "In progress", "Resolved")
# Severity given to an incident opened automatically from a visit.
AUTO_SEVERITY = "Medium"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _find(store, incident_id: str) -> dict:
    for inc in store.db.get("incidents", []):
        if inc["id"] == incident_id:
            return inc
    raise DomainError(404, "Incident not found.")


def _change(inc: dict, before) -> Change:
    return Change("incident", inc["id"], before, copy.deepcopy(inc), copy.deepcopy(inc))


@implements("REQ-SAFE-02")
def list_incidents(store, user: dict | None = None) -> list:
    """Incidents visible to a user, newest first."""
    with store.lock:
        incidents = copy.deepcopy(store.db.get("incidents", []))
    incidents.reverse()
    if user is None or user.get("role") == "coordinator":
        return incidents
    if user.get("role") == "caregiver":
        cid = user.get("caregiverId")
        return [i for i in incidents if i.get("caregiverId") == cid]
    rid = user.get("requestId")
    return [i for i in incidents if i.get("requestId") == rid]


@mutation("incident.created", "REQ-SAFE-02")
def create_incident(store, request_id: str, severity: str, description: str, visit_id: str | None = None, owner: str | None = None) -> Change:
    req = next((r for r in store.db["requests"] if r["id"] == request_id), None)
    if req is None:
        raise DomainError(404, "Request not found.")
    if severity not in SEVERITIES:
        raise DomainError(400, "Choose a severity: Low, Medium, High or Critical.")
    if not (description or "").strip():
        raise DomainError(400, "Describe what happened.")
    if visit_id is not None:
        visit = next((v for v in store.db["visits"] if v["id"] == visit_id), None)
        if visit is None:
            raise DomainError(404, "Visit not found.")
        if visit["requestId"] != request_id:
            raise DomainError(400, "That visit is not for this request.")
    inc = {
        "id": store.next_id("i"),
        "requestId": request_id,
        "visitId": visit_id,
        "caregiverId": req.get("caregiverId"),
        "severity": severity,
        "status": "Open",
        "owner": owner,
        "description": description.strip(),
        "resolution": None,
        "createdAt": _now_iso(),
        "resolvedAt": None,
    }
    store.db.setdefault("incidents", []).append(inc)
    return _change(inc, None)


@mutation("incident.opened", "REQ-SAFE-02")
def open_for_visit(store, visit: dict) -> Change:
    """Open an incident for a visit logged as "Needs attention".

    Raises 409 for any other mood (nothing to escalate) and for a visit that
    already has an incident, so one visit can never open two incidents.
    The owner is left empty: a coordinator assigns one (see assign_incident).
    """
    if visit.get("mood") != "Needs attention":
        raise DomainError(409, "An incident is only opened for a visit that needs attention.")
    if any(i.get("visitId") == visit["id"] for i in store.db.get("incidents", [])):
        raise DomainError(409, "An incident is already open for this visit.")
    inc = {
        "id": store.next_id("i"),
        "requestId": visit["requestId"],
        "visitId": visit["id"],
        "caregiverId": visit.get("caregiverId"),
        "severity": AUTO_SEVERITY,
        "status": "Open",
        "owner": None,
        "description": f"Needs attention visit: {visit.get('note', '')}".strip(),
        "resolution": None,
        "createdAt": _now_iso(),
        "resolvedAt": None,
    }
    store.db.setdefault("incidents", []).append(inc)
    return _change(inc, None)


@mutation("incident.assigned", "REQ-SAFE-02")
def assign_incident(store, incident_id: str, owner: str) -> Change:
    inc = _find(store, incident_id)
    if inc["status"] == "Resolved":
        raise DomainError(409, "This incident is already resolved.")
    owner = (owner or "").strip().lower()
    if not owner:
        raise DomainError(400, "Enter the owner's email address.")
    from .auth import find_user

    user = find_user(store.db, owner)
    if user is None or user.get("role") != "coordinator":
        raise DomainError(400, "The owner must be a coordinator account.")
    before = copy.deepcopy(inc)
    inc["owner"] = owner
    if inc["status"] == "Open":
        inc["status"] = "In progress"
    return _change(inc, before)


@mutation("incident.resolved", "REQ-SAFE-02")
def resolve_incident(store, incident_id: str, resolution: str) -> Change:
    inc = _find(store, incident_id)
    if inc["status"] == "Resolved":
        raise DomainError(409, "This incident is already resolved.")
    if not (resolution or "").strip():
        raise DomainError(400, "Describe how the incident was resolved.")
    before = copy.deepcopy(inc)
    inc["status"] = "Resolved"
    inc["resolution"] = resolution.strip()
    inc["resolvedAt"] = _now_iso()
    return _change(inc, before)
