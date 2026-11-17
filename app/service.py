"""Business rules. Every state change goes through @mutation, which writes the audit event,
the structured log line and the metric, so no change can skip observability or traceability."""
import copy
import functools
import logging
from datetime import date, datetime, timezone

from .auth import ROLES, find_user, hash_password, public_user
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


@implements("REQ-RBAC-01")
def get_state(store, user: dict | None = None) -> dict:
    """Full state for a coordinator (or no user, e.g. service-level callers).

    A caregiver sees only their own caregiver record (public fields, no date of
    birth), their matched requests and their own visits. A family sees only its
    own request, that request's visits, and its matched caregiver's public info.
    """
    with store.lock:
        # New modules are imported lazily: they import @mutation from here,
        # so importing them at module level would be circular.
        from .aftercare import defaults_for
        from .credentials import verification_stale
        from .privacy import consent_status_for

        data = {k: v for k, v in store.db.items() if k in ("caregivers", "requests", "visits")}
        state = copy.deepcopy({"meta": {"stages": STAGES, "notes": NOTES, "modules": MODULES, "moods": MOODS, "verification": [{"id": k, "label": l} for k, l in VERIFICATION], "verification_statuses": VERIFICATION_STATUSES}, **data})
        for c in state["caregivers"]:
            defaults_for(c)  # Needs dateOfBirth for the default 21st birthday.
            c.pop("dateOfBirth", None)
            # A background check whose credential has expired does not change
            # the verification record; it is surfaced as a flag (REQ-CRED-01).
            c["verificationStale"] = verification_stale(c)
            # REQ-PRIV-01: flag consent per care-leaver so a withdrawn (or missing)
            # consent is visible on the caregiver page.
            c["consentStatus"] = consent_status_for(store.db, "caregiver", c["id"])
        if user is None or user.get("role") == "coordinator":
            return state
        if user.get("role") == "caregiver":
            cid = user.get("caregiverId")
            state["caregivers"] = [c for c in state["caregivers"] if c["id"] == cid]
            request_ids = {r["id"] for r in state["requests"] if r.get("caregiverId") == cid}
            state["requests"] = [r for r in state["requests"] if r["id"] in request_ids]
            state["visits"] = [v for v in state["visits"] if v.get("caregiverId") == cid]
            return state
        # Family. Document reference numbers, housing, wellbeing check-ins and
        # child-protection contacts are personal to the care-leaver and are
        # never shown to a family (REQ-AFT-03); the care plan is the parent's
        # own plan, so it stays on the request.
        rid = user.get("requestId")
        matched = [r for r in state["requests"] if r["id"] == rid]
        caregiver_id = matched[0].get("caregiverId") if matched else None
        state["requests"] = matched
        state["visits"] = [v for v in state["visits"] if v["requestId"] == rid]
        state["caregivers"] = [c for c in state["caregivers"] if c["id"] == caregiver_id] if caregiver_id else []
        for c in state["caregivers"]:
            for key in ("documents", "housing", "checkins", "contacts"):
                c.pop(key, None)
        return state


def _strip_dob(obj) -> None:
    """Remove dateOfBirth keys, at any depth, from a copied audit payload."""
    if isinstance(obj, dict):
        obj.pop("dateOfBirth", None)
        for value in obj.values():
            _strip_dob(value)
    elif isinstance(obj, list):
        for value in obj:
            _strip_dob(value)


@implements("REQ-RBAC-01")
def scoped_audit(store, user: dict | None = None, entity_id: str | None = None, action: str | None = None, limit: int = 50) -> list:
    """Audit events visible to a user. A coordinator sees all; a caregiver sees
    events about their own caregiver record, matched requests and own visits; a
    family sees events about its own request and that request's visits.

    Date of birth is stripped from before/after payloads for non-coordinators,
    so it cannot leak through the audit log.
    """
    with store.lock:
        events = store.audit.query(entity_id, action, 500)
        if user is None or user.get("role") == "coordinator":
            visible = events
        elif user.get("role") == "caregiver":
            cid = user.get("caregiverId")
            keep = {cid}
            keep |= {r["id"] for r in store.db["requests"] if r.get("caregiverId") == cid}
            keep |= {v["id"] for v in store.db["visits"] if v.get("caregiverId") == cid}
            visible = [e for e in events if e["entity_id"] in keep]
        else:  # Family.
            rid = user.get("requestId")
            keep = {rid}
            keep |= {v["id"] for v in store.db["visits"] if v["requestId"] == rid}
            visible = [e for e in events if e["entity_id"] in keep]
        out = copy.deepcopy(visible[:limit])
        if user is not None and user.get("role") != "coordinator":
            for e in out:
                _strip_dob(e.get("before"))
                _strip_dob(e.get("after"))
        return out


@mutation("user.created", "REQ-RBAC-01")
def create_user(store, email: str, role: str, password: str, caregiver_id: str | None = None, request_id: str | None = None) -> Change:
    email = (email or "").strip().lower()
    if "@" not in email:
        raise DomainError(400, "Enter a valid email address.")
    if role not in ROLES:
        raise DomainError(400, "Unknown role.")
    if len(password or "") < 8:
        raise DomainError(400, "Password must be at least 8 characters.")
    if find_user(store.db, email) is not None:
        raise DomainError(409, "An account with this email already exists.")
    if role == "caregiver":
        if not caregiver_id:
            raise DomainError(400, "A caregiver account needs a caregiver to link to.")
        _find(store, "caregivers", caregiver_id, "Care-leaver")
    if role == "family":
        if not request_id:
            raise DomainError(400, "A family account needs a request to link to.")
        _find(store, "requests", request_id, "Request")
    salt, digest = hash_password(password)
    user = {"email": email, "role": role, "caregiverId": caregiver_id, "requestId": request_id, "password_salt": salt, "password_hash": digest}
    store.db.setdefault("users", []).append(user)
    public = public_user(user)
    return Change("user", email, None, copy.deepcopy(public), copy.deepcopy(public))


@mutation("caregiver.created", "REQ-PIPE-01", "REQ-AFT-02")
def add_caregiver(store, name: str, city: str, date_of_birth: date, today: date | None = None) -> Change:
    today = today or _today()
    if date_of_birth > today:
        raise DomainError(400, "Date of birth cannot be in the future.")
    if _age(date_of_birth, today) < MIN_AGE:
        raise DomainError(400, "Care-leavers must be 18 or older.")
    c = {"id": store.next_id("c"), "name": name, "city": city, "dateOfBirth": date_of_birth.isoformat(), "stage": "Applied", "modules": [0] * len(MODULES), "verification": _blank_checks()}
    # New care-leavers start with the aftercare defaults (window ends on the
    # 21st birthday). Lazy import: aftercare imports @mutation from here.
    from .aftercare import defaults_for

    defaults_for(c)
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


@mutation("request.created", "REQ-FAM-01", "REQ-MATCH-02")
def create_request(store, family: str, parent: str, city: str, visits_per_week: int, need: str, language: str | None = None) -> Change:
    r = {"id": store.next_id("r"), "family": family, "parent": parent, "city": city, "visitsPerWeek": visits_per_week, "need": need, "language": language, "status": "Open", "caregiverId": None, "matchedAt": None}
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
    r["matchedAt"] = _today().isoformat()  # Reports count full weeks since this date (REQ-REP-01).
    return Change("request", r["id"], before, {"request": copy.deepcopy(r), "caregiver": copy.deepcopy(c)}, copy.deepcopy(r))


@mutation("visit.logged", "REQ-VIS-01", "REQ-EVV-01")
def log_visit(store, request_id: str, mood: str, note: str, planned_at=None) -> Change:
    r = _find(store, "requests", request_id, "Request")
    if r["status"] != "Matched":
        raise DomainError(409, "Visits can only be logged for matched requests.")
    if mood not in MOODS:
        raise DomainError(400, "Choose how the visit went.")
    v = {"id": store.next_id("v"), "requestId": r["id"], "caregiverId": r["caregiverId"], "date": datetime.now(timezone.utc).date().isoformat(), "mood": mood, "note": note, "plannedAt": _planned_iso(planned_at)}
    store.db["visits"].insert(0, v)
    return Change("visit", v["id"], None, copy.deepcopy(v), copy.deepcopy(v))


def _planned_iso(planned_at) -> str:
    """The planned visit time as an ISO string. Defaults to now, so a visit
    logged without a planned time is never late (REQ-EVV-01)."""
    if planned_at is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(planned_at, datetime):
        return planned_at.isoformat()
    try:
        return datetime.fromisoformat(str(planned_at)).isoformat()
    except ValueError:
        raise DomainError(400, "Planned time is not valid.") from None
