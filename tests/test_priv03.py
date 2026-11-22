"""Full erasure vs. audit history (REQ-PRIV-03): per-person encryption of
audit payloads ("crypto-shredding") and the one-time plaintext migration.

Service-level tests use Store() directly; API tests use TestClient + login,
following tests/test_api.py. Caregiver dates of birth here are inside the
aftercare window (REQ-AFT-04), so the matched fixtures stay valid.
"""
import json
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import erasure, privacy, service
from app.audit import AuditLog
from app.domain import Change, Ctx
from app.main import create_app
from app.seed import seed
from app.store import Store
from tests._trace import covers

CTX = Ctx("req-1", "t" * 32, "coordinator@saathi.local")
DOB = date(2007, 5, 5)
SEED_PASSWORD = "test-seed-password"
SEED_EMAIL = "coordinator@saathi.local"


def make():
    return Store()


def login(client, email=SEED_EMAIL, password=SEED_PASSWORD):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SAATHI_SEED_PASSWORD", SEED_PASSWORD)
    c = TestClient(create_app(tmp_path / "db.json", public_dir=None))
    c.headers["Authorization"] = f"Bearer {login(c)}"
    return c


def drive_to_matched(s, name="Divya Sharma", city="Pune"):
    """A new care-leaver driven to Certified + verified, matched to a request."""
    c = service.add_caregiver(s, CTX, name, city, DOB)
    service.start_training(s, CTX, c["id"])
    for i in range(5):
        service.toggle_module(s, CTX, c["id"], i)
    service.certify(s, CTX, c["id"])
    for item in ("identity", "background", "references"):
        service.set_verification(s, CTX, c["id"], item, "Verified")
    r = service.create_request(s, CTX, "Nair family", "Mrs. Nair", city, 2, "Companionship")
    service.match(s, CTX, r["id"], c["id"])
    return c, r


def raw_audit_json(s):
    return json.dumps(s.db["audit"], ensure_ascii=False)


@covers("REQ-PRIV-03")
def test_encrypt_decrypt_round_trip_and_tamper():
    key = erasure._get_or_create_key({"personKeys": {}}, "c1")
    env = erasure.encrypt_value(key, "c1", "Divya Sharma")
    assert env["enc"] == "hmac-ctr-v1" and env["kid"] == "c1"
    assert erasure.decrypt_value(key, env) == "Divya Sharma"
    # A flipped ciphertext byte fails authentication, not silent decryption.
    tampered = dict(env, ct=env["ct"][:-2] + ("AA" if env["ct"][-2:] != "AA" else "BB"))
    assert erasure.decrypt_value(key, tampered) is None
    # The wrong person's key also fails.
    other = erasure._get_or_create_key({"personKeys": {}}, "c2")
    assert erasure.decrypt_value(other, env) is None
    # A malformed envelope fails closed.
    assert erasure.decrypt_value(key, {"enc": "hmac-ctr-v1"}) is None


@covers("REQ-PRIV-03")
def test_earlier_audit_events_stop_exposing_the_name_after_erasure():
    s = make()
    c = service.add_caregiver(s, CTX, "Divya Sharma", "Pune", DOB)
    privacy.grant_consent(s, CTX, "caregiver", c["id"], ["care matching"])
    service.start_training(s, CTX, c["id"])
    # Before erasure the name is readable through audit reads.
    created = next(e for e in s.audit.query() if e["action"] == "caregiver.created")
    assert created["after"]["name"] == "Divya Sharma"
    assert created["after"]["dateOfBirth"] == "2007-05-05"
    # But the stored events already hold ciphertext, not the name.
    assert "Divya Sharma" not in raw_audit_json(s)
    assert "2007-05-05" not in raw_audit_json(s)

    privacy.erase_caregiver(s, CTX, c["id"])

    # The key is gone, so the earlier events can no longer be read back...
    assert c["id"] not in s.db.get("personKeys", {})
    events = s.audit.query()
    created = next(e for e in events if e["action"] == "caregiver.created")
    assert created["after"]["name"] == "[erased]"
    assert created["after"]["dateOfBirth"] == "[erased]"
    # ...and the raw stored events never expose the name either.
    assert "Divya Sharma" not in raw_audit_json(s)
    assert "2007-05-05" not in raw_audit_json(s)
    # The chain still verifies end to end, and the erasure event is there.
    assert s.audit.verify() == {"ok": True, "events": len(s.db["audit"]), "broken_at": None}
    assert any(e["action"] == "caregiver.erased" for e in events)


@covers("REQ-PRIV-03")
def test_erasing_one_person_keeps_the_other_party_readable():
    s = make()
    c, r = drive_to_matched(s)
    privacy.erase_caregiver(s, CTX, c["id"])
    events = s.audit.query()
    matched = next(e for e in events if e["action"] == "request.matched")
    # The erased care-leaver's name is gone from the shared event...
    assert matched["after"]["caregiver"]["name"] == "[erased]"
    # ...but the parent's name, under the parent's own key, still reads.
    assert matched["after"]["request"]["parent"] == "Mrs. Nair"
    assert matched["after"]["request"]["family"] == "Nair family"
    assert s.audit.verify()["ok"] is True


@covers("REQ-PRIV-03")
def test_visit_notes_stay_under_the_parent_key_after_caregiver_erasure():
    s = make()
    c, r = drive_to_matched(s)
    service.log_visit(s, CTX, r["id"], "Good", "Called mum, all well")
    privacy.erase_caregiver(s, CTX, c["id"])
    events = s.audit.query()
    logged = next(e for e in events if e["action"] == "visit.logged")
    assert logged["after"]["note"] == "Called mum, all well"
    assert s.audit.verify()["ok"] is True


@covers("REQ-PRIV-03")
def test_key_deletion_is_scoped_to_the_erased_person():
    s = make()
    c, r = drive_to_matched(s)
    assert c["id"] in s.db["personKeys"]
    assert r["id"] in s.db["personKeys"]
    privacy.erase_caregiver(s, CTX, c["id"])
    assert c["id"] not in s.db["personKeys"]
    assert r["id"] in s.db["personKeys"]


def _write_legacy_store(path):
    """A store file as written before REQ-PRIV-03: plaintext audit events,
    no personKeys, no auditCrypto marker. Returns the old head hash."""
    data = seed()
    data["caregivers"].append({
        "id": "c7", "name": "Divya Sharma", "city": "Pune", "stage": "Applied",
        "modules": [False] * 5, "dateOfBirth": "2007-05-05",
        "verification": {}, "createdAt": "2026-11-17",
    })
    events = []
    log = AuditLog(events)
    log.append(
        Ctx("req-1", "t" * 32, "coordinator@saathi.local"),
        "caregiver.created",
        Change("caregiver", "c7", None, {
            "id": "c7", "name": "Divya Sharma", "city": "Pune",
            "dateOfBirth": "2007-05-05", "stage": "Applied",
        }, {"id": "c7"}),
        ("REQ-PIPE-01",),
    )
    log.append(
        Ctx("req-1", "t" * 32, "coordinator@saathi.local"),
        "caregiver.training_started",
        Change("caregiver", "c7", {"stage": "Applied"}, {"stage": "Training"}, {"stage": "Training"}),
        ("REQ-PIPE-01",),
    )
    data["audit"] = events
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.executemany(
            "INSERT OR REPLACE INTO state(key, value) VALUES (?, ?)",
            [(k, json.dumps(v, ensure_ascii=False)) for k, v in data.items()],
        )
        conn.commit()
    finally:
        conn.close()
    return events[-1]["hash"]


def _read_raw_audit(path):
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT value FROM state WHERE key = 'audit'").fetchone()
    finally:
        conn.close()
    return row[0]


@covers("REQ-PRIV-03")
def test_migration_rewrites_legacy_plaintext_events(tmp_path):
    path = tmp_path / "saathi.sqlite"
    old_head = _write_legacy_store(path)
    assert "Divya Sharma" in _read_raw_audit(path)

    s = Store(path)
    assert s.db["auditCrypto"] == {"version": 1, "migratedEvents": 2}
    # The stored events no longer hold the name in plaintext...
    assert "Divya Sharma" not in _read_raw_audit(path)
    assert "2007-05-05" not in _read_raw_audit(path)
    # ...but reads decrypt it back, because the migration created the key.
    created = next(e for e in s.audit.query() if e["action"] == "caregiver.created")
    assert created["after"]["name"] == "Divya Sharma"
    # The chain verifies, and the anchor records the rewrite.
    assert s.audit.verify()["ok"] is True
    anchor = next(e for e in s.audit.query() if e["action"] == "audit.migrated")
    assert anchor["after"]["migratedEvents"] == 2
    assert anchor["after"]["previousHeadHash"] == old_head
    assert anchor["entity_id"] == "audit"
    # Reopening does not migrate again: no second anchor, same event count.
    count = len(s.db["audit"])
    s2 = Store(path)
    assert len(s2.db["audit"]) == count
    assert len([e for e in s2.db["audit"] if e["action"] == "audit.migrated"]) == 1
    assert s2.audit.verify()["ok"] is True


@covers("REQ-PRIV-03")
def test_fresh_store_is_marked_without_an_anchor(tmp_path):
    path = tmp_path / "saathi.sqlite"
    s = Store(path)
    assert s.db["auditCrypto"] == {"version": 1, "migratedEvents": 0}
    assert s.db["audit"] == []
    assert s.db.get("personKeys") == {}
    s2 = Store(path)
    assert s2.db["audit"] == []
    assert s2.audit.verify() == {"ok": True, "events": 0, "broken_at": None}


@covers("REQ-PRIV-03")
def test_erasure_hides_the_name_through_the_api(client):
    cid = client.post(
        "/api/caregivers", json={"name": "Divya Sharma", "city": "Pune", "dateOfBirth": "2007-05-05"}
    ).json()["id"]
    client.post(f"/api/caregivers/{cid}/start")
    r = client.post(f"/api/caregivers/{cid}/erase")
    assert r.status_code == 200, r.text
    events = client.get("/api/audit").json()
    assert events, "expected audit events"
    assert "Divya Sharma" not in json.dumps(events)
    assert "2007-05-05" not in json.dumps(events)
    created = next(e for e in events if e["action"] == "caregiver.created")
    assert created["after"]["name"] == "[erased]"
    assert client.get("/api/audit/verify").json()["ok"] is True
