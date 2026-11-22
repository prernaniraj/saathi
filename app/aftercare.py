"""Aftercare records for care-leavers: sending institution, eligibility window,
linked child-protection contacts (REQ-AFT-01), enforced 23-year aftercare cap
(REQ-AFT-04), identity documents, housing and wellbeing check-ins (REQ-AFT-03)."""
import copy
from datetime import date, datetime, timezone

from .domain import Change, DomainError
from .service import mutation
from .trace import implements


def _as_date(value: object, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise DomainError(400, f"{field} is not a valid date.") from None


def _find(store, caregiver_id: str) -> dict:
    for c in store.db["caregivers"]:
        if c["id"] == caregiver_id:
            return c
    raise DomainError(404, "Care-leaver not found.")


def _result(c: dict) -> dict:
    """What a caregiver mutation returns: the record without date of birth."""
    return copy.deepcopy({k: v for k, v in c.items() if k != "dateOfBirth"})


def _change(c: dict, before) -> Change:
    return Change("caregiver", c["id"], before, copy.deepcopy(c), _result(c))


def twenty_first_birthday(date_of_birth: str | None) -> str | None:
    """The default end of the aftercare window: the 21st birthday."""
    if not date_of_birth:
        return None
    try:
        born = date.fromisoformat(date_of_birth)
    except ValueError:
        return None
    try:
        return born.replace(year=born.year + 21).isoformat()
    except ValueError:  # 29 February: fall back to 28 February.
        return born.replace(year=born.year + 21, day=28).isoformat()


def twenty_third_birthday(date_of_birth: str | None) -> str | None:
    """The end of the aftercare window when the extension is recorded: the
    23rd birthday."""
    if not date_of_birth:
        return None
    try:
        born = date.fromisoformat(date_of_birth)
    except ValueError:
        return None
    try:
        return born.replace(year=born.year + 23).isoformat()
    except ValueError:  # 29 February: fall back to 28 February.
        return born.replace(year=born.year + 23, day=28).isoformat()


@implements("REQ-AFT-04")
def aftercare_end(c: dict) -> date | None:
    """The last day of a care-leaver's aftercare window, or None when no
    window can be computed (no date of birth and no recorded until date).

    A recorded ``aftercareUntil`` wins; otherwise the window ends on the
    21st birthday, or the 23rd when the extension is recorded."""
    until = c.get("aftercareUntil")
    if until:
        try:
            return date.fromisoformat(until)
        except ValueError:
            return None
    dob = c.get("dateOfBirth")
    if not dob:
        return None
    if c.get("extended"):
        return _as_date(twenty_third_birthday(dob), "Aftercare until")
    return _as_date(twenty_first_birthday(dob), "Aftercare until")


@implements("REQ-AFT-04")
def aftercare_expired(c: dict, today: date | None = None) -> bool:
    """True once a care-leaver's aftercare window has ended. The end date
    itself is expired: on the 21st birthday (or the 23rd, when extended) the
    window is over, not the day after."""
    end = aftercare_end(c)
    if end is None:
        return False
    return (today or datetime.now(timezone.utc).date()) >= end


@implements("REQ-AFT-01", "REQ-AFT-03")
def defaults_for(c: dict) -> dict:
    """Fill the aftercare/profile fields for a caregiver created before these
    features existed. Works on a copy (get_state passes one); it never writes
    to the stored record. The default aftercare window ends on the 21st
    birthday; an extension to 23 is recorded explicitly, not assumed. When
    the extension is recorded but no until date is stored, the window ends
    on the 23rd birthday (REQ-AFT-04)."""
    c.setdefault("institution", None)
    if c.get("aftercareUntil") is None:
        c["aftercareUntil"] = (
            twenty_third_birthday(c.get("dateOfBirth"))
            if c.get("extended")
            else twenty_first_birthday(c.get("dateOfBirth"))
        )
    c.setdefault("extended", False)
    c.setdefault("contacts", [])
    c.setdefault("locationConsent", False)
    c.setdefault("documents", [])
    c.setdefault("housing", None)
    c.setdefault("checkins", [])
    c.setdefault("credentials", [])
    c.setdefault("languages", [])
    c.setdefault("availability", {"days": [], "hours": ""})
    c.setdefault("capacityVisitsPerWeek", None)
    return c


@mutation("caregiver.aftercare_set", "REQ-AFT-01")
def set_aftercare(store, caregiver_id: str, institution: str | None = None, aftercare_until=None, extended: bool | None = None, contacts: list | None = None, location_consent: bool | None = None) -> Change:
    """Set the sending institution, the aftercare eligibility window and the
    linked child-protection contacts. Every argument is optional: only the
    fields given are changed. ``locationConsent`` (whether the caregiver agrees
    to location being recorded at check-in) lives here because it is a consent
    the coordinator records with the aftercare record."""
    c = _find(store, caregiver_id)
    if institution is not None and not str(institution).strip():
        raise DomainError(400, "Institution name is not valid.")
    if contacts is not None:
        if not isinstance(contacts, list):
            raise DomainError(400, "Contacts must be a list.")
        for contact in contacts:
            if not isinstance(contact, dict) or not all(str(contact.get(k, "")).strip() for k in ("name", "role", "phone")):
                raise DomainError(400, "Each contact needs a name, a role and a phone number.")
    until = _as_date(aftercare_until, "Aftercare until")
    before = copy.deepcopy(c)
    if institution is not None:
        c["institution"] = str(institution).strip()
    if until is not None:
        c["aftercareUntil"] = until.isoformat()
    elif extended is True and c.get("dateOfBirth"):
        # REQ-AFT-04: recording the extension moves the stored window to the
        # 23rd birthday, so the recorded window and the flag agree; an
        # explicit date still wins over the computed one.
        end23 = twenty_third_birthday(c["dateOfBirth"])
        if end23 is not None:
            c["aftercareUntil"] = end23
    if extended is not None:
        c["extended"] = bool(extended)
    if contacts is not None:
        c["contacts"] = copy.deepcopy(contacts)
    if location_consent is not None:
        c["locationConsent"] = bool(location_consent)
    return _change(c, before)


DOCUMENT_STATUSES = ("Pending", "Verified", "Rejected")


@mutation("caregiver.document_added", "REQ-AFT-03")
def add_document(store, caregiver_id: str, doc_type: str, reference: str, status: str = "Pending") -> Change:
    """Record an identity document. The reference number is personal data: it
    is returned only to the coordinator and to the caregiver themself (see
    get_state), never in a family-scoped view."""
    c = _find(store, caregiver_id)
    if not str(doc_type or "").strip():
        raise DomainError(400, "Document type is required.")
    if not str(reference or "").strip():
        raise DomainError(400, "Document reference is required.")
    if status not in DOCUMENT_STATUSES:
        raise DomainError(400, "Unknown document status.")
    before = copy.deepcopy(c)
    c.setdefault("documents", []).append({"type": str(doc_type).strip(), "reference": str(reference).strip(), "status": status})
    return _change(c, before)


HOUSING_STATUSES = ("Stable", "Temporary", "At risk", "Unknown")


@mutation("caregiver.housing_set", "REQ-AFT-03")
def set_housing(store, caregiver_id: str, status: str, details: str | None = None) -> Change:
    c = _find(store, caregiver_id)
    if status not in HOUSING_STATUSES:
        raise DomainError(400, "Unknown housing status.")
    before = copy.deepcopy(c)
    c["housing"] = {"status": status, "details": (details or "").strip() or None}
    return _change(c, before)


@mutation("caregiver.checkin_added", "REQ-AFT-03")
def add_checkin(store, caregiver_id: str, note: str, checkin_date=None) -> Change:
    """A wellbeing check-in note. Defaults to today when no date is given."""
    c = _find(store, caregiver_id)
    if not str(note or "").strip():
        raise DomainError(400, "Check-in note is required.")
    day = _as_date(checkin_date, "Check-in date") or datetime.now(timezone.utc).date()
    before = copy.deepcopy(c)
    c.setdefault("checkins", []).append({"date": day.isoformat(), "note": str(note).strip()})
    return _change(c, before)
