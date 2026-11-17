"""Business rules. Every state change goes through @mutation, which writes the audit event,
the structured log line and the metric, so no change can skip observability or traceability."""
import copy
import functools
import logging

from .domain import MODULES, MOODS, NOTES, STAGES, Change, Ctx, DomainError
from .observability import event, metrics
from .trace import implements
from datetime import datetime, timezone


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


def _find(store, collection: str, item_id: str, label: str) -> dict:
    for item in store.db[collection]:
        if item["id"] == item_id:
            return item
    raise DomainError(404, f"{label} not found.")


def get_state(store) -> dict:
    with store.lock:
        data = {k: v for k, v in store.db.items() if k in ("caregivers", "requests", "visits")}
        return copy.deepcopy({"meta": {"stages": STAGES, "notes": NOTES, "modules": MODULES, "moods": MOODS}, **data})


@mutation("caregiver.created", "REQ-PIPE-01")
def add_caregiver(store, name: str, city: str) -> Change:
    c = {"id": store.next_id("c"), "name": name, "city": city, "stage": "Applied", "modules": [0] * len(MODULES)}
    store.db["caregivers"].append(c)
    return Change("caregiver", c["id"], None, copy.deepcopy(c), copy.deepcopy(c))


@mutation("caregiver.training_started", "REQ-PIPE-02")
def start_training(store, caregiver_id: str) -> Change:
    c = _find(store, "caregivers", caregiver_id, "Care-leaver")
    if c["stage"] != "Applied":
        raise DomainError(409, "Training can only start from the Applied stage.")
    before = copy.deepcopy(c)
    c["stage"] = "Training"
    return Change("caregiver", c["id"], before, copy.deepcopy(c), copy.deepcopy(c))


@mutation("caregiver.module_toggled", "REQ-PIPE-03")
def toggle_module(store, caregiver_id: str, index: int) -> Change:
    c = _find(store, "caregivers", caregiver_id, "Care-leaver")
    if not 0 <= index < len(MODULES):
        raise DomainError(400, "Unknown module.")
    if c["stage"] != "Training":
        raise DomainError(409, "Modules can only be updated during training.")
    before = copy.deepcopy(c)
    c["modules"][index] = 0 if c["modules"][index] else 1
    return Change("caregiver", c["id"], before, copy.deepcopy(c), copy.deepcopy(c))


@mutation("caregiver.certified", "REQ-PIPE-04")
def certify(store, caregiver_id: str) -> Change:
    c = _find(store, "caregivers", caregiver_id, "Care-leaver")
    if c["stage"] != "Training":
        raise DomainError(409, "Only care-leavers in training can be certified.")
    if not all(c["modules"]):
        raise DomainError(409, "Complete all five modules before certifying.")
    before = copy.deepcopy(c)
    c["stage"] = "Certified"
    return Change("caregiver", c["id"], before, copy.deepcopy(c), copy.deepcopy(c))


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
