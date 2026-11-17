"""Consent, notice, retention and erasure. REQ-PRIV-01.

A consent record covers one person (a care-leaver, or a parent identified by
their family request id) and the purposes their data may be used for. Consent
can be withdrawn; erasure replaces a care-leaver's personal fields with
placeholders while keeping the id, stage and modules so requests, visits and
the audit chain still refer to something.

Erasure audit safety: the audit event for an erasure is built with redacted
before/after payloads that contain only placeholders, never the erased
personal data. Note the known limit this does not fix: earlier audit events
(e.g. the original ``caregiver.created``) still hold the name, because the
hash chain cannot be rewritten. See SPEC.md section 8.
"""
import copy
from datetime import datetime, timezone

from .domain import Change, DomainError
from .service import mutation
from .trace import implements

NOTICE_VERSION = "1"
PURPOSES = ("care matching", "visit updates to family", "training records")
PERSON_TYPES = ("caregiver", "parent")
ERASED = "[erased]"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def consent_status_for(db: dict, person_type: str, person_id: str) -> str:
    """Latest consent status for a person: Granted, Withdrawn, or None."""
    for con in reversed(db.get("consents", [])):
        if con["personType"] == person_type and con["personId"] == person_id:
            return con["status"]
    return "None"


def _check_person(store, person_type: str, person_id: str) -> None:
    if person_type == "caregiver":
        if not any(c["id"] == person_id for c in store.db["caregivers"]):
            raise DomainError(404, "Care-leaver not found.")
    else:  # A parent is identified by their family request id.
        if not any(r["id"] == person_id for r in store.db["requests"]):
            raise DomainError(404, "Request not found.")


@implements("REQ-PRIV-01")
def list_consents(store, user: dict | None = None) -> list:
    """Consent records visible to a user, newest first."""
    with store.lock:
        consents = copy.deepcopy(store.db.get("consents", []))
    consents.reverse()
    if user is None or user.get("role") == "coordinator":
        return consents
    if user.get("role") == "caregiver":
        cid = user.get("caregiverId")
        return [c for c in consents if c["personType"] == "caregiver" and c["personId"] == cid]
    rid = user.get("requestId")
    return [c for c in consents if c["personType"] == "parent" and c["personId"] == rid]


@mutation("consent.granted", "REQ-PRIV-01")
def grant_consent(store, person_type: str, person_id: str, purposes, notice_version: str = NOTICE_VERSION) -> Change:
    if person_type not in PERSON_TYPES:
        raise DomainError(400, "Person type must be caregiver or parent.")
    _check_person(store, person_type, person_id)
    purposes = list(purposes or [])
    if not purposes:
        raise DomainError(400, "Choose at least one purpose.")
    unknown = [p for p in purposes if p not in PURPOSES]
    if unknown:
        raise DomainError(400, f"Unknown purpose: {', '.join(unknown)}.")
    for con in store.db.get("consents", []):
        if con["personType"] == person_type and con["personId"] == person_id and con["status"] == "Granted":
            raise DomainError(409, "Consent is already granted for this person.")
    con = {
        "id": store.next_id("con"),
        "personType": person_type,
        "personId": person_id,
        "purposes": purposes,
        "status": "Granted",
        "grantedAt": _now_iso(),
        "withdrawnAt": None,
        "noticeVersion": notice_version,
    }
    store.db.setdefault("consents", []).append(con)
    return Change("consent", con["id"], None, copy.deepcopy(con), copy.deepcopy(con))


@mutation("consent.withdrawn", "REQ-PRIV-01")
def withdraw_consent(store, consent_id: str) -> Change:
    con = next((c for c in store.db.get("consents", []) if c["id"] == consent_id), None)
    if con is None:
        raise DomainError(404, "Consent record not found.")
    if con["status"] != "Granted":
        raise DomainError(409, "This consent is already withdrawn.")
    before = copy.deepcopy(con)
    con["status"] = "Withdrawn"
    con["withdrawnAt"] = _now_iso()
    return Change("consent", con["id"], before, copy.deepcopy(con), copy.deepcopy(con))


def _redacted(caregiver: dict, erased: bool) -> dict:
    """A copy of a caregiver record with no personal data in it.

    Name and city are placeholders, date of birth is absent. Stage, modules
    and verification stay: they are needed to keep referential integrity and
    are not personal data on their own.
    """
    return {
        "id": caregiver["id"],
        "name": ERASED,
        "city": ERASED,
        "stage": caregiver["stage"],
        "modules": list(caregiver.get("modules", [])),
        "verification": copy.deepcopy(caregiver.get("verification", {})),
        "erased": erased,
        "erasedAt": caregiver.get("erasedAt") if erased else None,
    }


@mutation("caregiver.erased", "REQ-PRIV-01")
def erase_caregiver(store, caregiver_id: str) -> Change:
    """Erase a care-leaver's personal data.

    Name and city become "[erased]" and date of birth is removed; id, stage
    and modules are kept so requests, visits and the audit chain still refer
    to the record. Any granted consent for the person is withdrawn as part of
    the same change. The audit event's before/after payloads are built from
    the redacted copy only, so the erased personal data never lands in the
    audit log via this event.
    """
    c = next((x for x in store.db["caregivers"] if x["id"] == caregiver_id), None)
    if c is None:
        raise DomainError(404, "Care-leaver not found.")
    if c.get("erased"):
        raise DomainError(409, "This record has already been erased.")
    before = _redacted(c, erased=False)
    for con in store.db.get("consents", []):
        if con["personType"] == "caregiver" and con["personId"] == caregiver_id and con["status"] == "Granted":
            con["status"] = "Withdrawn"
            con["withdrawnAt"] = _now_iso()
    c["name"] = ERASED
    c["city"] = ERASED
    c.pop("dateOfBirth", None)
    c["erased"] = True
    c["erasedAt"] = _now_iso()
    after = _redacted(c, erased=True)
    return Change("caregiver", c["id"], before, after, copy.deepcopy(after))
