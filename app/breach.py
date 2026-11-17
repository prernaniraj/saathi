"""Breach register with a 72-hour Board-report clock. REQ-PRIV-02.

A breach records when it was discovered; the Board must be told within 72
hours of discovery, so ``boardDueAt = discoveredAt + 72h`` and the read view
carries an ``overdue`` flag (now past the due time while ``boardReportedAt``
is still empty) plus the hours remaining. The order is fixed: notify the
affected people first, then report to the Board.
"""
import copy
from datetime import datetime, timedelta, timezone

from .domain import Change, DomainError
from .service import mutation
from .trace import implements

BOARD_REPORT_HOURS = 72
STATUSES = ("Open", "Notified", "Reported", "Closed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _parse_dt(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise DomainError(400, "Discovered at must be an ISO date and time.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def board_due_at(discovered_at: str) -> datetime:
    """The Board-report deadline: discovery plus 72 hours."""
    return _parse_dt(discovered_at) + timedelta(hours=BOARD_REPORT_HOURS)


def breach_view(breach: dict, now: datetime | None = None) -> dict:
    """A copy with the clock fields computed at read time."""
    now = now or _now()
    due = board_due_at(breach["discoveredAt"])
    view = copy.deepcopy(breach)
    view["boardDueAt"] = due.isoformat()
    view["overdue"] = breach.get("boardReportedAt") is None and now > due
    view["hoursRemaining"] = round((due - now).total_seconds() / 3600, 1)
    return view


def _find(store, breach_id: str) -> dict:
    for b in store.db.get("breaches", []):
        if b["id"] == breach_id:
            return b
    raise DomainError(404, "Breach not found.")


@implements("REQ-PRIV-02")
def list_breaches(store) -> list:
    """All breaches, newest first, with the clock fields. Coordinator only
    (enforced by the route)."""
    with store.lock:
        breaches = copy.deepcopy(store.db.get("breaches", []))
    breaches.reverse()
    return [breach_view(b) for b in breaches]


@mutation("breach.created", "REQ-PRIV-02")
def create_breach(store, title: str, description: str, affected_person_ids=None, discovered_at: str | None = None) -> Change:
    if not (title or "").strip():
        raise DomainError(400, "Enter a title for the breach.")
    if not (description or "").strip():
        raise DomainError(400, "Describe what happened.")
    affected = list(affected_person_ids or [])
    for person_id in affected:
        known = any(c["id"] == person_id for c in store.db["caregivers"]) or any(r["id"] == person_id for r in store.db["requests"])
        if not known:
            raise DomainError(404, f"Affected person not found: {person_id}.")
    discovered = _parse_dt(discovered_at).isoformat() if discovered_at else _now_iso()
    b = {
        "id": store.next_id("b"),
        "title": title.strip(),
        "description": description.strip(),
        "discoveredAt": discovered,
        "affectedPersonIds": affected,
        "notifiedAt": None,
        "boardReportedAt": None,
        "status": "Open",
    }
    store.db.setdefault("breaches", []).append(b)
    return Change("breach", b["id"], None, copy.deepcopy(b), breach_view(b))


@mutation("breach.notified", "REQ-PRIV-02")
def notify_breach(store, breach_id: str) -> Change:
    b = _find(store, breach_id)
    if b["status"] != "Open":
        raise DomainError(409, "The affected people have already been notified for this breach.")
    before = copy.deepcopy(b)
    b["status"] = "Notified"
    b["notifiedAt"] = _now_iso()
    return Change("breach", b["id"], before, copy.deepcopy(b), breach_view(b))


@mutation("breach.reported", "REQ-PRIV-02")
def report_breach(store, breach_id: str) -> Change:
    b = _find(store, breach_id)
    if b["status"] == "Open":
        raise DomainError(409, "Notify the affected people before reporting to the Board.")
    if b["status"] in ("Reported", "Closed"):
        raise DomainError(409, "This breach has already been reported to the Board.")
    before = copy.deepcopy(b)
    b["status"] = "Reported"
    b["boardReportedAt"] = _now_iso()
    return Change("breach", b["id"], before, copy.deepcopy(b), breach_view(b))
