"""Credentials and background-check expiry (REQ-CRED-01).

A credential is {id, type, issuedDate, expiryDate, status}; status is computed
when the credential is added ("Valid", or "Expired" when the expiry date has
passed). The expiring list is computed from the expiry date, not the stored
status, so it stays correct as time passes.

A background check whose credential has expired does NOT silently change the
caregiver's verification status: it is surfaced as a ``verificationStale``
flag (in get_state and in the expiring list) so a coordinator can re-verify
deliberately. See SPEC.md section 8.
"""
import copy
from datetime import date, datetime, timezone

from .domain import Change, DomainError
from .service import mutation
from .trace import implements


def _as_date(value, field: str) -> date:
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


def _status(expiry: date, today: date) -> str:
    return "Expired" if expiry < today else "Valid"


@mutation("caregiver.credential_added", "REQ-CRED-01")
def add_credential(store, caregiver_id: str, cred_type: str, expiry_date, issued_date=None, today: date | None = None) -> Change:
    c = _find(store, caregiver_id)
    if not str(cred_type or "").strip():
        raise DomainError(400, "Credential type is required.")
    expiry = _as_date(expiry_date, "Expiry date")
    issued = _as_date(issued_date, "Issued date") if issued_date is not None else None
    if issued is not None and expiry < issued:
        raise DomainError(400, "Expiry date cannot be before the issued date.")
    today = today or datetime.now(timezone.utc).date()
    before = copy.deepcopy(c)
    cred = {
        "id": store.next_id("k"),
        "type": str(cred_type).strip(),
        "issuedDate": issued.isoformat() if issued else None,
        "expiryDate": expiry.isoformat(),
        "status": _status(expiry, today),
    }
    c.setdefault("credentials", []).append(cred)
    return Change("caregiver", c["id"], before, copy.deepcopy(c), copy.deepcopy({k: v for k, v in c.items() if k != "dateOfBirth"}))


@implements("REQ-CRED-01")
def verification_stale(caregiver: dict, today: date | None = None) -> bool:
    """True when a "Background check" credential has passed its expiry date.
    The verification record itself is left untouched; this flag is what the
    coordinator acts on."""
    today = today or datetime.now(timezone.utc).date()
    for cred in caregiver.get("credentials", []) or []:
        if cred.get("type") == "Background check":
            try:
                if date.fromisoformat(cred["expiryDate"]) < today:
                    return True
            except (ValueError, TypeError, KeyError):
                continue
    return False


@implements("REQ-CRED-01")
def expiring_credentials(store, days: int = 30, today: date | None = None) -> list:
    """Credentials expiring within N days, soonest first. Already-expired
    credentials are included (with a negative daysLeft) so an expired
    background check is never silently dropped from the alert list."""
    today = today or datetime.now(timezone.utc).date()
    out = []
    for c in store.db["caregivers"]:
        for cred in c.get("credentials", []) or []:
            try:
                expiry = date.fromisoformat(cred["expiryDate"])
            except (ValueError, TypeError, KeyError):
                continue
            days_left = (expiry - today).days
            if days_left <= days:
                entry = {"caregiverId": c["id"], "caregiverName": c["name"], "credential": copy.deepcopy(cred), "daysLeft": days_left, "verificationStale": verification_stale(c, today)}
                out.append(entry)
    out.sort(key=lambda e: e["credential"]["expiryDate"])
    return out
