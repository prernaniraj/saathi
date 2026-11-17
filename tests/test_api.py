"""HTTP-level tests. Need dev extras: pip install -e '.[dev]' && pytest"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests._trace import covers


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(tmp_path / "db.json", public_dir=None))


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
    r = client.post("/api/caregivers/c1/start", headers={"X-Request-ID": "trace-me", "X-Actor": "coordinator"})
    assert r.status_code == 200
    events = client.get("/api/audit", params={"entity_id": "c1"}).json()
    assert events[0]["request_id"] == "trace-me" and events[0]["actor"] == "coordinator" and events[0]["spec_refs"] == ["REQ-PIPE-02"]


@covers("REQ-TRC-02")
def test_verify_endpoint_reports_intact_chain(client):
    client.post("/api/caregivers", json={"name": "Divya", "city": "Pune", "dateOfBirth": "2005-05-05"})
    assert client.get("/api/audit/verify").json() == {"ok": True, "events": 1, "broken_at": None}


@covers("REQ-PIPE-01", "REQ-PIPE-04", "REQ-MATCH-01", "REQ-VIS-01", "REQ-FAM-01", "REQ-SAFE-01")
def test_end_to_end_flow(client):
    cid = client.post("/api/caregivers", json={"name": "Divya", "city": "Pune", "dateOfBirth": "2005-05-05"}).json()["id"]
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
