"""Business rules. Every state change goes through @mutation, which writes the audit event,
the structured log line and the metric, so no change can skip observability or traceability."""
import copy
import functools
import logging
from datetime import date, datetime, timezone

from .domain import MODULES, MOODS, NOTES, STAGES, VERIFICATION, VERIFICATION_STATUSES, Change, Ctx, DomainError
from .observability import event, metrics
from .trace import implements


@implements("REQ-TRC-05")
def mutation(action: str, *spec_refs: str):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(store, ctx: Ctx, *args, **kwargs):
            with store.lock:
                try:
                    change = fn(store, *args, **kwargs)
                except DomainError as e:
                    metrics.inc("saathi_changes_rejected_total", action=action, status=e.status)
                    event(logging.WARNING, "change rejected", action=action, status=e.status, reason=e.message, actor=ctx.actor, spec_refs=list(spec_refs))
                    raise
                ev = store.audit.append(ctx, action, change, spec_refs)
                store.save()
            metrics.inc("saathi_changes_total", action=action)
            event(logging.INFO, "change committed", action=action, entity=f"{change.entity_type}:{change.entity_id}", audit_seq=ev["seq"], actor=ctx.actor, spec_refs=list(spec_refs))
            return change.result

        wrapper.reqs = spec_refs
        wrapper.action = action
        return wrapper

    return deco

MIN_AGE = 18


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _age(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _blank_checks() -> dict:
    return {key: {"status": "Pending", "date": None} for key, _ in VERIFICATION}


def is_cleared(c: dict) -> bool:
    """True when identity, background and reference checks are all Verified."""
    checks = c.get("verification", {})
    return all(checks.get(key, {}).get("status") == "Verified" for key, _ in VERIFICATION)


def _public(c: dict) -> dict:
    """A copy without personal data that callers do not need (date of birth)."""
    return copy.deepcopy({k: v for k, v in c.items() if k != "dateOfBirth"})


def _change(c: dict, before) -> Change:
    return Change("caregiver", c["id"], before, copy.deepcopy(c), _public(c))


def _find(store, collection: str, item_id: str, label: str) -> dict:
    for item in store.db[collection]:
        if item["id"] == item_id:
            return item
    raise DomainError(404, f"{label} not found.")


def get_state(store) -> dict:
    with store.lock:
        data = {k: v for k, v in store.db.items() if k in ("caregivers", "requests", "visits")}
        state = copy.deepcopy({"meta": {"stages": STAGES, "notes": NOTES, "modules": MODULES, "moods": MOODS, "verification": [{"id": k, "label": l} for k, l in VERIFICATION], "verification_statuses": VERIFICATION_STATUSES}, **data})
        for c in state["caregivers"]:
            c.pop("dateOfBirth", None)
        return state


@mutation("caregiver.created", "REQ-PIPE-01", "REQ-AFT-02")
def add_caregiver(store, name: str, city: str, date_of_birth: date, today: date | None = None) -> Change:
    today = today or _today()
    if date_of_birth > today:
        raise DomainError(400, "Date of birth cannot be in the future.")
    if _age(date_of_birth, today) < MIN_AGE:
        raise DomainError(400, "Care-leavers must be 18 or older.")
    c = {"id": store.next_id("c"), "name": name, "city": city, "dateOfBirth": date_of_birth.isoformat(), "stage": "Applied", "modules": [0] * len(MODULES), "verification": _blank_checks()}
    store.db["caregivers"].append(c)
    return _change(c, None)


@mutation("caregiver.verification_set", "REQ-SAFE-01")
def set_verification(store, caregiver_id: str, item: str, status: str, today: date | None = None) -> Change:
    c = _find(store, "caregivers", caregiver_id, "Care-leaver")
    if item not in {key for key, _ in VERIFICATION}:
        raise DomainError(400, "Unknown check.")
    if status not in VERIFICATION_STATUSES:
        raise DomainError(400, "Unknown status.")
    checks = copy.deepcopy(c.get("verification") or _blank_checks())
    if checks.get(item, {}).get("status", "Pending") == status:
        raise DomainError(409, "That check already has this status.")
    before = copy.deepcopy(c)
    checks[item] = {"status": status, "date": (today or _today()).isoformat()}
    c["verification"] = checks
    return _change(c, before)


@mutation("caregiver.training_started", "REQ-PIPE-02")
def start_training(store, caregiver_id: str) -> Change:
    c = _find(store, "caregivers", caregiver_id, "Care-leaver")
    if c["stage"] != "Applied":
        raise DomainError(409, "Training can only start from the Applied stage.")
    before = copy.deepcopy(c)
    c["stage"] = "Training"
    return _change(c, before)


@mutation("caregiver.module_toggled", "REQ-PIPE-03")
def toggle_module(store, caregiver_id: str, index: int) -> Change:
    c = _find(store, "caregivers", caregiver_id, "Care-leaver")
    if not 0 <= index < len(MODULES):
        raise DomainError(400, "Unknown module.")
    if c["stage"] != "Training":
        raise DomainError(409, "Modules can only be updated during training.")
    before = copy.deepcopy(c)
    c["modules"][index] = 0 if c["modules"][index] else 1
    return _change(c, before)


@mutation("caregiver.certified", "REQ-PIPE-04")
def certify(store, caregiver_id: str) -> Change:
    c = _find(store, "caregivers", caregiver_id, "Care-leaver")
    if c["stage"] != "Training":
        raise DomainError(409, "Only care-leavers in training can be certified.")
    if not all(c["modules"]):
        raise DomainError(409, "Complete all five modules before certifying.")
    before = copy.deepcopy(c)
    c["stage"] = "Certified"
    return _change(c, before)


@mutation("request.created", "REQ-FAM-01")
def create_request(store, family: str, parent: str, city: str, visits_per_week: int, need: str) -> Change:
    r = {"id": store.next_id("r"), "family": family, "parent": parent, "city": city, "visitsPerWeek": visits_per_week, "need": need, "status": "Open", "caregiverId": None}
    store.db["requests"].append(r)
    return Change("request", r["id"], None, copy.deepcopy(r), copy.deepcopy(r))


@mutation("request.matched", "REQ-MATCH-01")
def match(store, request_id: str, caregiver_id: str) -> Change:
    r = _find(store, "requests", request_id, "Request")
    c = _find(store, "caregivers", caregiver_id, "Caregiver")
    if r["status"] != "Open":
        raise DomainError(409, "This request already has a caregiver.")
    if c["stage"] != "Certified":
        raise DomainError(409, "Only certified caregivers can be matched.")
    if not is_cleared(c):
        raise DomainError(409, "Complete the identity, background and reference checks before matching.")
    before = {"request": copy.deepcopy(r), "caregiver": copy.deepcopy(c)}
    r["status"], r["caregiverId"], c["stage"] = "Matched", c["id"], "Matched"
    return Change("request", r["id"], before, {"request": copy.deepcopy(r), "caregiver": copy.deepcopy(c)}, copy.deepcopy(r))


@mutation("visit.logged", "REQ-VIS-01")
def log_visit(store, request_id: str, mood: str, note: str) -> Change:
    r = _find(store, "requests", request_id, "Request")
    if r["status"] != "Matched":
        raise DomainError(409, "Visits can only be logged for matched requests.")
    if mood not in MOODS:
        raise DomainError(400, "Choose how the visit went.")
    v = {"id": store.next_id("v"), "requestId": r["id"], "caregiverId": r["caregiverId"], "date": datetime.now(timezone.utc).date().isoformat(), "mood": mood, "note": note}
    store.db["visits"].insert(0, v)
    return Change("visit", v["id"], None, copy.deepcopy(v), copy.deepcopy(v))
