"""HTTP-level tests. Need dev extras: pip install -e '.[dev]' && pytest"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests._trace import covers

SEED_PASSWORD = "test-coordinator-password"
SEED_EMAIL = "coordinator@saathi.local"


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


@pytest.fixture()
def anon_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SAATHI_SEED_PASSWORD", SEED_PASSWORD)
    return TestClient(create_app(tmp_path / "db.json", public_dir=None))


def as_user(client, email, password):
    """Return a client authed as a newly logged-in user (shares the same app)."""
    client.headers["Authorization"] = f"Bearer {login(client, email, password)}"
    return client


@covers("REQ-OBS-01")
def test_request_id_is_echoed_or_generated(client):
    assert client.get("/healthz", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"] == "abc-123"
    assert len(client.get("/healthz").headers["x-request-id"]) == 32


@covers("REQ-OBS-01")
def test_traceparent_trace_id_is_reused(client):
    tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    assert client.get("/healthz", headers={"traceparent": tp}).headers["x-trace-id"] == "4bf92f3577b34da6a3ce929d0e0e4736"


@covers("REQ-OBS-04")
def test_health_and_readiness(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200


@covers("REQ-OBS-03")
def test_metrics_endpoint_counts_requests_and_changes(client):
    client.post("/api/caregivers/c1/start")
    text = client.get("/metrics").text
    assert 'saathi_changes_total{action="caregiver.training_started"}' in text
    assert "saathi_http_requests_total" in text


@covers("REQ-TRC-01", "REQ-TRC-03")
def test_change_is_traceable_from_request_id_to_audit_event(client):
    r = client.post("/api/caregivers/c1/start", headers={"X-Request-ID": "trace-me", "X-Actor": "someone-else"})
    assert r.status_code == 200
    events = client.get("/api/audit", params={"entity_id": "c1"}).json()
    assert events[0]["request_id"] == "trace-me" and events[0]["actor"] == SEED_EMAIL and events[0]["spec_refs"] == ["REQ-PIPE-02"]


@covers("REQ-TRC-02")
def test_verify_endpoint_reports_intact_chain(client):
    client.post("/api/caregivers", json={"name": "Divya", "city": "Pune", "dateOfBirth": "2005-05-05"})
    assert client.get("/api/audit/verify").json() == {"ok": True, "events": 1, "broken_at": None}


@covers("REQ-PIPE-01", "REQ-PIPE-04", "REQ-MATCH-01", "REQ-VIS-01", "REQ-FAM-01", "REQ-SAFE-01")
def test_end_to_end_flow(client):
    cid = client.post("/api/caregivers", json={"name": "Divya", "city": "Pune", "dateOfBirth": "2007-05-05"}).json()["id"]
    assert client.post(f"/api/caregivers/{cid}/certify").status_code == 409
    client.post(f"/api/caregivers/{cid}/start")
    for i in range(5):
        client.post(f"/api/caregivers/{cid}/modules/{i}")
    assert client.post(f"/api/caregivers/{cid}/certify").json()["stage"] == "Certified"
    rid = client.post("/api/requests", json={"family": "Nair", "parent": "Mrs. Nair", "city": "Pune", "visitsPerWeek": "2", "need": "Companionship"}).json()["id"]
    assert client.post(f"/api/requests/{rid}/match", json={"caregiverId": cid}).status_code == 409
    for item in ("identity", "background", "references"):
        assert client.post(f"/api/caregivers/{cid}/verification/{item}", json={"status": "Verified"}).status_code == 200
    assert client.post(f"/api/requests/{rid}/match", json={"caregiverId": cid}).json()["status"] == "Matched"
    v = client.post("/api/visits", json={"requestId": rid, "mood": "Good", "note": "Tea and a walk."})
    assert v.status_code == 201
    assert client.get("/api/audit/verify").json()["ok"] is True


def test_errors_use_one_shape_and_status_codes(client):
    assert client.post("/api/caregivers", json={"name": " ", "city": "Pune"}).json() == {"error": "Name is required."}
    assert client.post("/api/requests", json={"family": "A", "parent": "B", "city": "C", "visitsPerWeek": 9, "need": "Companionship"}).status_code == 400
    assert client.post("/api/caregivers/nope/start").status_code == 404


@covers("REQ-AFT-02")
def test_under_eighteen_is_rejected_and_date_of_birth_is_not_returned(client):
    r = client.post("/api/caregivers", json={"name": "Kid", "city": "Pune", "dateOfBirth": "2015-01-01"})
    assert r.status_code == 400 and r.json() == {"error": "Care-leavers must be 18 or older."}
    ok = client.post("/api/caregivers", json={"name": "Adult", "city": "Pune", "dateOfBirth": "2005-05-05"})
    assert "dateOfBirth" not in ok.json()
    assert all("dateOfBirth" not in c for c in client.get("/api/state").json()["caregivers"])


@covers("REQ-RBAC-01")
def test_login_rejects_a_wrong_password_and_unknown_email(anon_client):
    assert anon_client.post("/api/auth/login", json={"email": SEED_EMAIL, "password": "wrong-password"}).status_code == 401
    assert anon_client.post("/api/auth/login", json={"email": "nobody@saathi.local", "password": SEED_PASSWORD}).status_code == 401


@covers("REQ-RBAC-01")
def test_api_routes_require_login(anon_client):
    for method, path in (("get", "/api/state"), ("get", "/api/audit"), ("post", "/api/caregivers"), ("post", "/api/requests"), ("post", "/api/visits")):
        r = getattr(anon_client, method)(path, **({"json": {}} if method == "post" else {}))
        assert r.status_code == 401, (method, path, r.status_code)
        assert r.json() == {"error": "Login required."}
    # Health, readiness and metrics stay open.
    assert anon_client.get("/healthz").status_code == 200
    assert anon_client.get("/readyz").status_code == 200
    assert anon_client.get("/metrics").status_code == 200


@covers("REQ-RBAC-01")
def test_logout_invalidates_the_token(client):
    assert client.post("/api/auth/logout").json() == {"ok": True}
    assert client.get("/api/state").status_code == 401


@covers("REQ-RBAC-01")
def test_only_the_coordinator_can_create_accounts(client):
    r = client.post("/api/auth/users", json={"email": "fam@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"})
    assert r.status_code == 201 and r.json()["role"] == "family"
    assert "password_hash" not in r.text and "password_salt" not in r.text
    assert client.post("/api/auth/users", json={"email": "fam@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"}).status_code == 409
    assert client.post("/api/auth/users", json={"email": "bad", "role": "family", "password": "family-pass-1", "requestId": "r1"}).status_code == 400
    assert client.post("/api/auth/users", json={"email": "cg@example.com", "role": "caregiver", "password": "short"}).status_code == 400
    as_user(client, "fam@example.com", "family-pass-1")
    assert client.post("/api/auth/users", json={"email": "x@example.com", "role": "family", "password": "x-pass-word", "requestId": "r1"}).status_code == 403


@covers("REQ-RBAC-01")
def test_family_sees_only_its_own_request_and_no_date_of_birth(client):
    client.post("/api/auth/users", json={"email": "menon@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"})
    as_user(client, "menon@example.com", "family-pass-1")
    state = client.get("/api/state").json()
    assert [r["id"] for r in state["requests"]] == ["r1"]
    assert [v["id"] for v in state["visits"]] == ["v1"]
    assert [c["id"] for c in state["caregivers"]] == ["c6"]
    assert "dateOfBirth" not in client.get("/api/state").text
    # A family cannot write, except submitting its own request.
    assert client.post("/api/caregivers", json={"name": "X", "city": "Pune", "dateOfBirth": "2005-05-05"}).status_code == 403
    assert client.post("/api/visits", json={"requestId": "r1", "mood": "Good", "note": "Hi."}).status_code == 403
    rid = client.post("/api/requests", json={"family": "Menon", "parent": "Mr. Raghavan", "city": "Pune", "visitsPerWeek": 2, "need": "Companionship"}).json()["id"]
    assert client.get("/api/state").json()["requests"][0]["id"] == rid


@covers("REQ-RBAC-01")
def test_caregiver_sees_only_self_and_logs_only_own_visits(client):
    client.post("/api/auth/users", json={"email": "imran@example.com", "role": "caregiver", "password": "care-pass-1", "caregiverId": "c6"})
    as_user(client, "imran@example.com", "care-pass-1")
    state = client.get("/api/state").json()
    assert [c["id"] for c in state["caregivers"]] == ["c6"]
    assert [r["id"] for r in state["requests"]] == ["r1"]
    assert [v["id"] for v in state["visits"]] == ["v1"]
    assert client.post("/api/caregivers/c1/start").status_code == 403
    assert client.post("/api/visits", json={"requestId": "r2", "mood": "Good", "note": "Not mine."}).status_code == 403
    v = client.post("/api/visits", json={"requestId": "r1", "mood": "Okay", "note": "Checked in, all well."})
    assert v.status_code == 201 and v.json()["caregiverId"] == "c6"


@covers("REQ-RBAC-01")
def test_audit_is_role_scoped_and_date_of_birth_is_stripped_for_family(client):
    cid = client.post("/api/caregivers", json={"name": "Divya", "city": "Pune", "dateOfBirth": "2007-05-05"}).json()["id"]
    client.post(f"/api/caregivers/{cid}/start")
    for i in range(5):
        client.post(f"/api/caregivers/{cid}/modules/{i}")
    client.post(f"/api/caregivers/{cid}/certify")
    for item in ("identity", "background", "references"):
        client.post(f"/api/caregivers/{cid}/verification/{item}", json={"status": "Verified"})
    rid = client.post("/api/requests", json={"family": "Nair", "parent": "Mrs. Nair", "city": "Pune", "visitsPerWeek": "2", "need": "Companionship"}).json()["id"]
    client.post(f"/api/requests/{rid}/match", json={"caregiverId": cid})
    client.post("/api/visits", json={"requestId": rid, "mood": "Good", "note": "Tea and a walk."})
    client.post("/api/auth/users", json={"email": "nair@example.com", "role": "family", "password": "family-pass-1", "requestId": rid})
    as_user(client, "nair@example.com", "family-pass-1")
    events = client.get("/api/audit").json()
    assert events and all(e["entity_id"] in {rid, *[v["id"] for v in client.get("/api/state").json()["visits"]]} for e in events)
    assert "dateOfBirth" not in client.get("/api/audit").text
    # The coordinator still sees the full chain, date of birth included.
    as_user(client, SEED_EMAIL, SEED_PASSWORD)
    assert "dateOfBirth" in client.get("/api/audit").text


@covers("REQ-RBAC-01")
def test_dev_no_auth_fallback_accepts_x_actor(tmp_path, monkeypatch):
    monkeypatch.setenv("SAATHI_SEED_PASSWORD", SEED_PASSWORD)
    monkeypatch.setenv("SAATHI_DEV_NO_AUTH", "1")
    c = TestClient(create_app(tmp_path / "db.json", public_dir=None))
    assert c.get("/api/state", headers={"X-Actor": "dev-coordinator"}).status_code == 200
    monkeypatch.delenv("SAATHI_DEV_NO_AUTH")
    assert c.get("/api/state", headers={"X-Actor": "dev-coordinator"}).status_code == 401
