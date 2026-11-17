"""Email+password login, roles and bearer tokens. Standard library only.

Passwords are hashed with PBKDF2-HMAC-SHA256 and a per-user salt. Tokens are
opaque values from ``secrets``; only their SHA-256 hash is stored, with a
7-day expiry. Sessions live in the same store as the rest of the data
(``db["users"]`` and ``db["sessions"]``), so they are persisted by ``Store.save()``
alongside everything else.
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

ROLES = ("coordinator", "caregiver", "family")
SEED_COORDINATOR_EMAIL = "coordinator@saathi.local"
TOKEN_TTL = timedelta(days=7)
_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Return (salt_hex, digest_hex) for a password."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS).hex()
    return salt, digest


def verify_password(password: str, salt: str, digest: str) -> bool:
    try:
        _, candidate = hash_password(password, salt)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, digest)


def public_user(user: dict) -> dict:
    """The fields of a user that may leave the server (never password material)."""
    return {"email": user["email"], "role": user["role"], "caregiverId": user.get("caregiverId"), "requestId": user.get("requestId")}


def find_user(db: dict, email: str) -> dict | None:
    wanted = (email or "").strip().lower()
    for user in db.get("users") or []:
        if user.get("email") == wanted:
            return user
    return None


def ensure_seed_coordinator(db: dict, seed_password: str | None = None, print_password: bool = False) -> str | None:
    """Make sure exactly one seed coordinator account exists.

    Returns the password that was set, or None if the account already existed.
    The password comes from ``SAATHI_SEED_PASSWORD`` when set; otherwise a
    random one is generated and, for a durable store, printed once to stdout
    at first boot. No fixed password is ever hardcoded.
    """
    users = db.setdefault("users", [])
    if any(u.get("email") == SEED_COORDINATOR_EMAIL for u in users):
        return None
    env_password = os.environ.get("SAATHI_SEED_PASSWORD") or None
    password = seed_password or env_password or secrets.token_urlsafe(16)
    salt, digest = hash_password(password)
    users.append({"email": SEED_COORDINATOR_EMAIL, "role": "coordinator", "caregiverId": None, "requestId": None, "password_salt": salt, "password_hash": digest})
    if print_password and not (seed_password or env_password):
        print(f"Seed coordinator: {SEED_COORDINATOR_EMAIL} / {password} — change after first login", flush=True)
    return password


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: dict, email: str) -> str:
    """Create a session for a user and return the raw bearer token."""
    now = datetime.now(timezone.utc)
    db.setdefault("sessions", [])
    db["sessions"][:] = [s for s in db["sessions"] if datetime.fromisoformat(s["expires"]) > now]
    token = secrets.token_urlsafe(32)
    db["sessions"].append({"token_hash": _token_hash(token), "email": email, "expires": (now + TOKEN_TTL).isoformat()})
    return token


def authenticate(db: dict, token: str) -> dict | None:
    """Return the user for a valid, unexpired bearer token, else None."""
    if not token:
        return None
    now = datetime.now(timezone.utc)
    for session in db.get("sessions") or []:
        if hmac.compare_digest(session.get("token_hash", ""), _token_hash(token)):
            if datetime.fromisoformat(session["expires"]) <= now:
                return None
            return find_user(db, session["email"])
    return None


def revoke_session(db: dict, token: str) -> bool:
    """Delete the session for a token. Returns True if one was removed."""
    if not token:
        return False
    before = len(db.get("sessions") or [])
    db["sessions"][:] = [s for s in db.get("sessions") or [] if not hmac.compare_digest(s.get("token_hash", ""), _token_hash(token))]
    return len(db["sessions"]) < before
