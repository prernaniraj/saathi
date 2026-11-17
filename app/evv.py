"""Electronic visit verification (REQ-EVV-01): check-in and check-out with
timestamps. Location is recorded only when the caregiver has consented
(``locationConsent`` on the caregiver record); a location sent without consent
is rejected with 400 and nothing is stored.

"Late" means a check-in more than 30 minutes after the visit's planned time.
The planned time is ``plannedAt`` on the visit (set at logging time, defaulting
to the moment the visit was logged).
"""
import copy
import logging
from datetime import date, datetime, timedelta, timezone

from . import notifications
from .domain import Change, DomainError
from .observability import event
from .service import mutation

LATE_AFTER = timedelta(minutes=30)


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            raise DomainError(400, "Timestamp is not valid.") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _find_visit(store, visit_id: str) -> dict:
    for v in store.db["visits"]:
        if v["id"] == visit_id:
            return v
    raise DomainError(404, "Visit not found.")


def _find_caregiver(store, caregiver_id: str) -> dict:
    for c in store.db["caregivers"]:
        if c["id"] == caregiver_id:
            return c
    raise DomainError(404, "Care-leaver not found.")


def is_late(visit: dict) -> bool:
    """True when the check-in is more than 30 minutes after the planned time.
    False when there is no check-in or no planned time to compare against."""
    planned, checkin = _parse_dt(visit.get("plannedAt")), _parse_dt(visit.get("checkinAt"))
    if planned is None or checkin is None:
        return False
    return checkin > planned + LATE_AFTER


def _check_location(caregiver: dict, location) -> None:
    if location is not None and not caregiver.get("locationConsent"):
        raise DomainError(400, "Location can only be recorded with the caregiver's consent.")


def _late_notifications(store, visit: dict, when: datetime) -> None:
    """Create the late-arrival notifications for a check-in that is late.
    Runs inside the check-in mutation, so the check-in's own audit event
    covers the change; the notifications themselves are also log lines."""
    req = next((r for r in store.db["requests"] if r["id"] == visit.get("requestId")), None)
    planned = _parse_dt(visit.get("plannedAt"))
    minutes = int((when - planned).total_seconds() // 60) if planned else 0
    parent = req["parent"] if req else visit.get("requestId")
    message = f"Late arrival: the visit with {parent} on {visit.get('date')} started {minutes} minutes after the planned time."
    notifications.append_notification(store, {"role": "coordinator"}, "late", message, created_at=when, visit_id=visit.get("id"))
    if req is not None:
        notifications.append_notification(store, {"role": "family", "requestId": req["id"]}, "late", message, created_at=when, visit_id=visit.get("id"))
    event(logging.INFO, "late arrival", visit=visit["id"], minutes_late=minutes, spec_refs=["REQ-NOTIF-01"])


@mutation("visit.checked_in", "REQ-EVV-01")
def check_in(store, visit_id: str, timestamp=None, location: str | None = None) -> Change:
    v = _find_visit(store, visit_id)
    if v.get("checkinAt"):
        raise DomainError(409, "This visit already has a check-in.")
    caregiver = _find_caregiver(store, v["caregiverId"])
    _check_location(caregiver, location)
    when = _parse_dt(timestamp) or datetime.now(timezone.utc)
    before = copy.deepcopy(v)
    v["checkinAt"] = when.isoformat()
    if location is not None:
        v["checkinLocation"] = str(location)
    if is_late(v):
        _late_notifications(store, v, when)
    return Change("visit", v["id"], before, copy.deepcopy(v), copy.deepcopy(v))


@mutation("visit.checked_out", "REQ-EVV-01")
def check_out(store, visit_id: str, timestamp=None, location: str | None = None) -> Change:
    v = _find_visit(store, visit_id)
    if not v.get("checkinAt"):
        raise DomainError(409, "Check in before checking out.")
    if v.get("checkoutAt"):
        raise DomainError(409, "This visit already has a check-out.")
    caregiver = _find_caregiver(store, v["caregiverId"])
    _check_location(caregiver, location)
    when = _parse_dt(timestamp) or datetime.now(timezone.utc)
    before = copy.deepcopy(v)
    v["checkoutAt"] = when.isoformat()
    if location is not None:
        v["checkoutLocation"] = str(location)
    return Change("visit", v["id"], before, copy.deepcopy(v), copy.deepcopy(v))
