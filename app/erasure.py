"""Per-person encryption for audit events ("crypto-shredding"). REQ-PRIV-03.

Problem: erasure (REQ-PRIV-01) redacts the live record and the erasure audit
event, but earlier audit events (for example the original ``caregiver.created``)
still hold the erased person's name, because the hash chain cannot be
rewritten without breaking verification.

Fix: personal data in audit event payloads is stored encrypted under a
per-person key. Erasure deletes that person's key, so the stored ciphertext
(and therefore every hash) stays exactly as it was and the chain still
verifies, while the name can no longer be read back: audit reads replace
fields whose key is gone with ``"[erased]"``.

Construction (standard library only): each person gets one random 32-byte
key, stored base64 in ``db["personKeys"]`` under their id (caregiver id,
request id, or account email). A value is encrypted with a HMAC-based
keystream (encrypt) and a separate HMAC tag (authenticate) -- encrypt-then-
MAC -- with a fresh 16-byte nonce per value. The stored envelope carries the
key id (``kid``), so a read can find the right key; the ``kid`` itself is an
opaque id, not personal data.

Scope rules: a payload field is encrypted only under the key of the person
it belongs to. The top-level key comes from the entity (caregiver, request,
user account, care plan -> its own id; visit, incident -> the parent's
request id). Inside a payload, a nested dict whose ``id`` is a known
caregiver or request id (or an email) switches the scope to that person, so
a match event encrypts the caregiver's name under the caregiver's key and
the parent's name under the parent's key. Deleting one person's key then
hides only their data; the other party's data in the same event still reads.

Honest limits (see SPEC.md section 8):
- Keys live in the same SQLite file as the ciphertext. This protects the
  application layer and any copy of the store taken after erasure; it does
  NOT protect against someone who copied the whole file before erasure.
- Events stored in plaintext before this change are re-encrypted by a
  one-time migration on first boot (see ``migrate_audit``); a backup taken
  before that migration still holds the plaintext names.
- Breach and consent payloads are deliberately not encrypted: a breach
  register must stay readable for the safety workflow, and consent records
  hold ids and statuses, not personal data.
- There is no parent-erasure flow yet, so a parent's (request) key is never
  deleted by this POC; erasing a care-leaver deletes only their key.
"""
import base64
import copy
import hashlib
import hmac
import logging
import secrets

from .domain import Change, Ctx
from .observability import event as _log_event
from .trace import implements

ENC_VERSION = "hmac-ctr-v1"
ERASED = "[erased]"
UNREADABLE = "[unreadable]"

# Audit payload fields that identify a person or hold their private notes.
# Everything else (ids, stages, statuses, counts) stays readable in the
# stored event, so role-scoped audit filtering keeps working on raw events.
# "family"/"parent" are the family and parent names on a request record.
PERSONAL_FIELDS = frozenset({
    "name", "city", "dateOfBirth", "parentName", "family", "parent", "email",
    "note", "details", "phone", "reference", "contact",
})

# Action prefix -> entity type, for deciding which person's key encrypts an
# event's payload. Actions with no entry here carry no personal data.
_ACTION_ENTITY = {
    "caregiver": "caregiver",
    "request": "request",
    "visit": "visit",
    "user": "user",
    "consent": "consent",
    "incident": "incident",
    "breach": "breach",
    "careplan": "careplan",
}


def _subkey(key: bytes, label: bytes) -> bytes:
    return hmac.new(key, label, hashlib.sha256).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(
            key, b"stream:" + nonce + counter.to_bytes(4, "big"), hashlib.sha256
        ).digest()
        counter += 1
    return bytes(out[:length])


def encrypt_value(key: bytes, key_id: str, plaintext: str) -> dict:
    """Encrypt one string value. Returns the stored envelope dict."""
    nonce = secrets.token_bytes(16)
    enc_key = _subkey(key, b"enc")
    mac_key = _subkey(key, b"mac")
    raw = plaintext.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(raw, _keystream(enc_key, nonce, len(raw))))
    tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    return {
        "enc": ENC_VERSION,
        "kid": key_id,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ct": base64.b64encode(ct).decode("ascii"),
        "tag": base64.b64encode(tag).decode("ascii"),
    }


def decrypt_value(key: bytes, envelope: dict) -> str | None:
    """Decrypt an envelope. None when the envelope is malformed or tampered."""
    try:
        nonce = base64.b64decode(envelope["nonce"])
        ct = base64.b64decode(envelope["ct"])
        tag = base64.b64decode(envelope["tag"])
    except (KeyError, ValueError, TypeError):
        return None
    mac_key = _subkey(key, b"mac")
    expected = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        return None
    enc_key = _subkey(key, b"enc")
    raw = bytes(a ^ b for a, b in zip(ct, _keystream(enc_key, nonce, len(ct))))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _get_or_create_key(db: dict, key_id: str) -> bytes:
    keys = db.setdefault("personKeys", {})
    raw = keys.get(key_id)
    if raw is None:
        raw = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        keys[key_id] = raw
    return base64.b64decode(raw)


@implements("REQ-PRIV-03")
def delete_person_key(db: dict, key_id: str) -> bool:
    """Delete a person's audit key, so their encrypted audit data can no
    longer be read back. Returns True when a key was actually deleted.

    Also marks the store for a VACUUM on the next save, so the deleted key
    bytes do not linger in SQLite freelist pages (``save()`` also runs with
    ``PRAGMA secure_delete=ON``).
    """
    keys = db.get("personKeys") or {}
    if key_id not in keys:
        return False
    del keys[key_id]
    db["_vacuum_needed"] = True
    return True


def _person_ids(db: dict) -> tuple[set, set]:
    caregivers = {c["id"] for c in db.get("caregivers", [])}
    requests = {r["id"] for r in db.get("requests", [])}
    return caregivers, requests


def _scope_for(value: dict, current: str | None, caregivers: set, requests: set) -> str | None:
    """The key scope for a nested dict: its own ``id`` when that id is a
    known caregiver, request, or account email; otherwise the parent scope."""
    ident = value.get("id")
    if isinstance(ident, str) and (ident in caregivers or ident in requests or "@" in ident):
        return ident
    return current


def _encrypt_tree(db, value, key_id, caregivers, requests):
    if isinstance(value, dict):
        scope = _scope_for(value, key_id, caregivers, requests)
        out = {}
        for k, v in value.items():
            if k in PERSONAL_FIELDS and isinstance(v, str) and v != ERASED and scope is not None:
                out[k] = encrypt_value(_get_or_create_key(db, scope), scope, v)
            else:
                out[k] = _encrypt_tree(db, v, scope, caregivers, requests)
        return out
    if isinstance(value, list):
        return [_encrypt_tree(db, item, key_id, caregivers, requests) for item in value]
    return value


def _top_key(entity_type: str, entity_id: str, payload) -> str | None:
    """Which person's key encrypts the top level of a change's payload."""
    if entity_type in ("caregiver", "request", "user", "careplan"):
        return entity_id
    if entity_type in ("visit", "incident"):
        if isinstance(payload, dict):
            return payload.get("requestId")
        return None
    return None


@implements("REQ-PRIV-03")
def encrypt_change(db: dict, change) -> tuple:
    """Return encrypted copies of a change's before/after audit payloads.

    The live state is untouched; only the copies that land in the audit log
    are encrypted. Personal fields whose value is already ``"[erased]"``
    are left alone (they are placeholders, not personal data).
    """
    caregivers, requests = _person_ids(db)
    payload = change.after if isinstance(change.after, dict) else change.before
    top = _top_key(change.entity_type, change.entity_id, payload)
    before = (
        _encrypt_tree(db, copy.deepcopy(change.before), top, caregivers, requests)
        if change.before is not None
        else None
    )
    after = (
        _encrypt_tree(db, copy.deepcopy(change.after), top, caregivers, requests)
        if change.after is not None
        else None
    )
    return before, after


def _decrypt_tree(value, keys: dict):
    if isinstance(value, dict):
        if value.get("enc") == ENC_VERSION:
            raw = keys.get(value.get("kid"))
            if raw is None:
                return ERASED
            try:
                key = base64.b64decode(raw)
            except (ValueError, TypeError):
                return UNREADABLE
            pt = decrypt_value(key, value)
            return pt if pt is not None else UNREADABLE
        return {k: _decrypt_tree(v, keys) for k, v in value.items()}
    if isinstance(value, list):
        return [_decrypt_tree(item, keys) for item in value]
    return value


@implements("REQ-PRIV-03")
def decrypt_event(keys: dict, event: dict) -> dict:
    """Return a copy of an audit event with encrypted payload fields
    decrypted. Fields whose key is gone (erased person) come back as
    ``"[erased]"``; fields that fail authentication come back as
    ``"[unreadable]"``. The stored event is never modified."""
    out = copy.deepcopy(event)
    keys = keys or {}
    for side in ("before", "after"):
        if isinstance(out.get(side), dict):
            out[side] = _decrypt_tree(out[side], keys)
    return out


@implements("REQ-PRIV-03")
def migrate_audit(db: dict) -> int:
    """One-time migration for audit events stored in plaintext before
    REQ-PRIV-03: re-encrypt their personal fields under per-person keys,
    re-chain the hashes over the new payloads, and append an anchor event
    recording the rewrite (action ``audit.migrated``) so the chain still
    verifies end to end. Returns the number of events migrated (0 when the
    store has no audit events yet).

    The migration is safe to run once: ``Store`` only calls it when the
    store has no ``auditCrypto`` marker, and sets the marker afterwards.
    A backup taken before this migration still holds the plaintext names;
    nothing can un-write a copy that already left the store.
    """
    # Deferred import: app/audit.py imports this module, so importing it
    # at the top of this file would be circular.
    from .audit import GENESIS, AuditLog, _digest

    events = db.get("audit") or []
    if not events:
        return 0
    count = len(events)
    old_head = events[-1].get("hash")
    caregivers, requests = _person_ids(db)
    for ev in events:
        entity_type = _ACTION_ENTITY.get(str(ev.get("action", "")).split(".", 1)[0])
        if entity_type is None:
            continue
        for side in ("before", "after"):
            payload = ev.get(side)
            if isinstance(payload, dict):
                top = _top_key(entity_type, ev.get("entity_id"), payload)
                ev[side] = _encrypt_tree(db, copy.deepcopy(payload), top, caregivers, requests)
    prev = GENESIS
    for ev in events:
        ev["prev_hash"] = prev
        ev["hash"] = _digest(ev)
        prev = ev["hash"]
    AuditLog(events, keys=db.get("personKeys") or {}).append(
        Ctx("migration", "0" * 32, "system"),
        "audit.migrated",
        Change(
            "audit",
            "audit",
            None,
            {"migratedEvents": count, "previousHeadHash": old_head},
            {"migratedEvents": count},
        ),
        ("REQ-PRIV-03",),
    )
    _log_event(
        logging.INFO,
        "audit migrated",
        migrated_events=count,
        previous_head_hash=(old_head or "")[:16],
    )
    return count
