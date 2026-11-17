"""Visit escalation wiring (merge step). Logging a visit flagged
"Needs attention" opens an incident and notifies the coordinator and the
family; any other mood opens nothing. Needs dev extras: pytest."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests._trace import covers


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SAATHI_SEED_PASSWORD", "hook-test-pass")
    return TestClient(create_app(tmp_path / "db.sqlite", public_dir=None))


def _login(client):
    r = client.post("/api/auth/login", json={"email": "coordinator@saathi.local", "password": "hook-test-pass"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@covers("REQ-SAFE-02", "REQ-NOTIF-01")
def test_needs_attention_visit_opens_incident_and_notifies(client):
    h = _login(client)
    # Seed r1 is Matched to c6, so a visit can be logged for it.
    before_incidents = client.get("/api/incidents", headers=h).json()
    before_notes = client.get("/api/notifications", headers=h).json()
    v = client.post("/api/visits", headers=h, json={"requestId": "r1", "mood": "Needs attention", "note": "Missed medication."})
    assert v.status_code == 201
    incidents = client.get("/api/incidents", headers=h).json()
    assert len(incidents) == len(before_incidents) + 1
    opened = incidents[0]
    assert (opened["visitId"], opened["severity"], opened["status"]) == (v.json()["id"], "Medium", "Open")
    notes = client.get("/api/notifications", headers=h).json()
    flagged = [n for n in notes if n["kind"] == "needs_attention" and n.get("visitId") == v.json()["id"]]
    assert len(flagged) == 2  # coordinator + family
    assert {n["audience"]["role"] for n in flagged} == {"coordinator", "family"}


@covers("REQ-SAFE-02", "REQ-NOTIF-01")
def test_good_visit_opens_nothing(client):
    h = _login(client)
    before_incidents = client.get("/api/incidents", headers=h).json()
    v = client.post("/api/visits", headers=h, json={"requestId": "r1", "mood": "Good", "note": "Tea and a walk."})
    assert v.status_code == 201
    assert client.get("/api/incidents", headers=h).json() == before_incidents
    notes = client.get("/api/notifications", headers=h).json()
    assert not [n for n in notes if n["kind"] == "needs_attention" and n.get("visitId") == v.json()["id"]]
