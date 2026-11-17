"""In-app notifications (REQ-NOTIF-01).

There is no email or SMS provider in this proof of concept: notifications are
stored in the state, listed role-scoped through GET /api/notifications, and
each one also writes a structured log line. A real delivery channel (email or
SMS to the family abroad and the coordinator) is an open decision, recorded
in SPEC.md section 8.
"""
import copy
import logging
from datetime import date, datetime, timezone

from .domain import Change, DomainError
from .observability import event
from .service import mutation
from .trace import implements

KINDS = ("needs_attention", "late", "missed")


def append_notification(store, audience: dict, kind: str, message: str, created_at=None, visit_id: str | None = None) -> dict:
    """Append a notification to the store without its own audit event. Used
    inside another mutation (e.g. a late check-in), whose audit event covers
    the whole change. Standalone notifications go through the @mutation
    helpers below instead."""
    if kind not in KINDS:
        raise DomainError(400, "Unknown notification kind.")
    if isinstance(created_at, datetime):
        created = created_at.isoformat()
    elif isinstance(created_at, str):
        created = created_at
    else:
        created = datetime.now(timezone.utc).isoformat()
    n = {"id": store.next_id("n"), "audience": dict(audience), "kind": kind, "message": message, "createdAt": created, "readAt": None}
    if visit_id:
        n["visitId"] = visit_id
    store.db.setdefault("notifications", []).append(n)
    event(logging.INFO, "notification created", kind=kind, audience=audience, notification_id=n["id"])
    return copy.deepcopy(n)


def _visible_to(n: dict, user: dict | None) -> bool:
    if user is None or user.get("role") == "coordinator":
        return True
    audience = n.get("audience", {})
    if user.get("role") == "caregiver":
        return audience.get("role") == "caregiver" and audience.get("caregiverId") == user.get("caregiverId")
    if user.get("role") == "family":
        return audience.get("role") == "family" and audience.get("requestId") == user.get("requestId")
    return False


@implements("REQ-NOTIF-01")
def visible_notifications(store, user: dict | None = None) -> list:
    """Notifications a user may see, newest first. The coordinator sees all;
    a caregiver sees only their own; a family sees only its request's."""
    with store.lock:
        notes = [copy.deepcopy(n) for n in store.db.get("notifications", []) if _visible_to(n, user)]
    notes.sort(key=lambda n: n["createdAt"], reverse=True)
    return notes


@mutation("notification.created", "REQ-NOTIF-01")
def notify_for_visit(store, visit: dict) -> Change:
    """Notify the coordinator and the family of the request when a visit is
    flagged "Needs attention". A visit with any other mood creates nothing.
    The coordinator wires this in after log_visit during the merge; it is not
    called from log_visit itself."""
    created = []
    if visit.get("mood") == "Needs attention":
        req = next((r for r in store.db["requests"] if r["id"] == visit.get("requestId")), None)
        parent = req["parent"] if req else visit.get("requestId")
        message = f"Visit on {visit.get('date')} with {parent} was flagged 'Needs attention': {visit.get('note', '')}"
        created.append(append_notification(store, {"role": "coordinator"}, "needs_attention", message, visit_id=visit.get("id")))
        if req is not None:
            created.append(append_notification(store, {"role": "family", "requestId": req["id"]}, "needs_attention", message, visit_id=visit.get("id")))
    return Change("notifications", "visit-flag", None, copy.deepcopy(created), copy.deepcopy(created))


@mutation("notification.late_created", "REQ-NOTIF-01")
def notify_late_arrival(store, visit: dict, minutes_late: int) -> Change:
    """Standalone late-arrival notification for a visit. The check-in path in
    app/evv.py creates the same notification inline; this helper exists for
    re-checks. It never creates a second unread late notification for the
    same visit."""
    created = []
    already = [n for n in store.db.get("notifications", []) if n.get("kind") == "late" and n.get("visitId") == visit.get("id") and n.get("readAt") is None]
    if not already:
        req = next((r for r in store.db["requests"] if r["id"] == visit.get("requestId")), None)
        parent = req["parent"] if req else visit.get("requestId")
        message = f"Late arrival: the visit with {parent} on {visit.get('date')} started {minutes_late} minutes after the planned time."
        created.append(append_notification(store, {"role": "coordinator"}, "late", message, visit_id=visit.get("id")))
        if req is not None:
            created.append(append_notification(store, {"role": "family", "requestId": req["id"]}, "late", message, visit_id=visit.get("id")))
    return Change("notifications", "late-flag", None, copy.deepcopy(created), copy.deepcopy(created))


@mutation("notification.missed_checked", "REQ-NOTIF-01")
def check_missed_visits(store, today: date | None = None) -> Change:
    """Check every Matched request for a missed visit. The expected interval
    is 7 / visitsPerWeek days since the last logged visit; a request with no
    visit logged yet has no baseline, so it is skipped (see SPEC section 8).
    A request is flagged only when it is overdue, and never twice while an
    unread missed-visit notification for it exists."""
    today = today or datetime.now(timezone.utc).date()
    created = []
    for r in store.db["requests"]:
        if r.get("status") != "Matched":
            continue
        visits = [v for v in store.db["visits"] if v.get("requestId") == r["id"]]
        if not visits:
            continue
        last = max(date.fromisoformat(v["date"]) for v in visits)
        interval = 7 / r["visitsPerWeek"]
        if (today - last).days <= interval:
            continue
        unread = [n for n in store.db.get("notifications", []) if n.get("kind") == "missed" and n.get("audience", {}).get("requestId") == r["id"] and n.get("readAt") is None]
        if unread:
            continue
        message = f"No visit logged for {r['parent']} in {(today - last).days} days; one is expected about every {interval:.1f} days."
        created.append(append_notification(store, {"role": "coordinator"}, "missed", message))
        created.append(append_notification(store, {"role": "family", "requestId": r["id"]}, "missed", message))
    return Change("notifications", "missed-check", None, copy.deepcopy(created), copy.deepcopy(created))


@mutation("notification.read", "REQ-NOTIF-01")
def mark_read(store, notification_id: str, user: dict) -> Change:
    """Mark one notification read. A user may only mark their own visible
    notifications read; marking an already-read one is a no-op."""
    n = next((x for x in store.db.get("notifications", []) if x["id"] == notification_id), None)
    if n is None:
        raise DomainError(404, "Notification not found.")
    if not _visible_to(n, user):
        raise DomainError(403, "Your role cannot do this.")
    before = copy.deepcopy(n)
    if n.get("readAt") is None:
        n["readAt"] = datetime.now(timezone.utc).isoformat()
    return Change("notification", n["id"], before, copy.deepcopy(n), copy.deepcopy(n))
