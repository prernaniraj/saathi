"""Safety and privacy tests (P0): incidents, consent/erasure, breach clock.

Service-level tests use Store() directly; API tests use TestClient + login,
following tests/test_api.py.
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import breach as breach_mod
from app import incidents, privacy, service
from app.domain import Ctx, DomainError
from app.main import create_app
from app.store import Store
from tests._trace import covers

CTX = Ctx("req-1", "t" * 32, "coordinator@saathi.local")
DOB = date(2005, 5, 5)
SEED_PASSWORD = "test-coordinator-password"
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


def as_user(client, email, password):
    client.headers["Authorization"] = f"Bearer {login(client, email, password)}"
    return client


def matched_store():
    """A store with a matched request r1 (caregiver c6) and a Needs attention visit."""
    s = make()
    visit = service.log_visit(s, CTX, "r1", "Needs attention", "Missed medication, seemed confused.")
    return s, visit


# --- REQ-SAFE-02: incidents -------------------------------------------------

@covers("REQ-SAFE-02")
def test_needs_attention_visit_opens_an_incident():
    s, visit = matched_store()
    inc = incidents.open_for_visit(s, CTX, visit)
    assert inc["status"] == "Open"
    assert inc["severity"] == "Medium"
    assert inc["visitId"] == visit["id"] and inc["requestId"] == "r1" and inc["caregiverId"] == "c6"
    assert inc["owner"] is None  # a coordinator assigns the owner
    assert "Missed medication" in inc["description"]
    ev = s.db["audit"][-1]
    assert (ev["action"], ev["spec_refs"]) == ("incident.opened", ["REQ-SAFE-02"])


@covers("REQ-SAFE-02")
def test_helper_refuses_other_moods_and_a_second_incident_for_one_visit():
    s = make()
    good = service.log_visit(s, CTX, "r1", "Good", "Tea and a walk.")
    with pytest.raises(DomainError) as e:
        incidents.open_for_visit(s, CTX, good)
    assert e.value.status == 409
    assert s.db["incidents"] == []
    _, visit = matched_store()
    incidents.open_for_visit(s, CTX, visit)
    with pytest.raises(DomainError) as e:
        incidents.open_for_visit(s, CTX, visit)
    assert e.value.status == 409
    assert len(s.db["incidents"]) == 1


@covers("REQ-SAFE-02")
def test_assign_and_resolve_flow():
    s, visit = matched_store()
    inc = incidents.open_for_visit(s, CTX, visit)
    with pytest.raises(DomainError) as e:
        incidents.assign_incident(s, CTX, inc["id"], "not-a-coordinator@example.com")
    assert e.value.status == 400
    assigned = incidents.assign_incident(s, CTX, inc["id"], SEED_EMAIL)
    assert (assigned["owner"], assigned["status"]) == (SEED_EMAIL, "In progress")
    assert s.db["audit"][-1]["action"] == "incident.assigned"
    resolved = incidents.resolve_incident(s, CTX, inc["id"], "Called the family; doctor visit booked.")
    assert resolved["status"] == "Resolved"
    assert resolved["resolution"] == "Called the family; doctor visit booked."
    assert resolved["resolvedAt"]
    with pytest.raises(DomainError) as e:
        incidents.resolve_incident(s, CTX, inc["id"], "Again.")
    assert e.value.status == 409


@covers("REQ-SAFE-02")
def test_incident_api_create_assign_resolve_and_role_rules(client):
    r = client.post("/api/incidents", json={"requestId": "r1", "severity": "High", "description": "Parent fell, no injury."})
    assert r.status_code == 201, r.text
    inc = r.json()
    assert (inc["status"], inc["owner"]) == ("Open", SEED_EMAIL)
    assert client.post(f"/api/incidents/{inc['id']}/assign", json={"owner": "nobody@example.com"}).status_code == 400
    assert client.post(f"/api/incidents/{inc['id']}/assign", json={"owner": SEED_EMAIL}).json()["status"] == "In progress"
    assert client.post(f"/api/incidents/{inc['id']}/resolve", json={"resolution": "Checked in, all well."}).json()["status"] == "Resolved"
    assert client.post(f"/api/incidents/{inc['id']}/resolve", json={"resolution": "Again."}).status_code == 409
    assert client.post("/api/incidents", json={"requestId": "nope", "severity": "High", "description": "x"}).status_code == 404
    assert client.post("/api/incidents", json={"requestId": "r1", "severity": "Extreme", "description": "x"}).status_code == 400
    # A caregiver may read incidents about their own visits, not create them.
    # Both extra accounts are created up front, while still the coordinator.
    client.post("/api/auth/users", json={"email": "imran@example.com", "role": "caregiver", "password": "care-pass-1", "caregiverId": "c6"})
    client.post("/api/auth/users", json={"email": "menon@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"})
    as_user(client, "imran@example.com", "care-pass-1")
    assert [i["id"] for i in client.get("/api/incidents").json()] == [inc["id"]]
    assert client.post("/api/incidents", json={"requestId": "r1", "severity": "Low", "description": "x"}).status_code == 403
    assert client.post(f"/api/incidents/{inc['id']}/assign", json={"owner": SEED_EMAIL}).status_code == 403
    assert client.post(f"/api/incidents/{inc['id']}/resolve", json={"resolution": "x"}).status_code == 403
    # A family sees only incidents about its own request.
    as_user(client, "menon@example.com", "family-pass-1")
    assert [i["id"] for i in client.get("/api/incidents").json()] == [inc["id"]]
    assert client.post("/api/incidents", json={"requestId": "r1", "severity": "Low", "description": "x"}).status_code == 403


# --- REQ-PRIV-01: consent and erasure --------------------------------------

@covers("REQ-PRIV-01")
def test_consent_grant_withdraw_and_regrant():
    s = make()
    # A new care-leaver has no seed consent, so granting works.
    cid = service.add_caregiver(s, CTX, "Divya", "Pune", DOB)["id"]
    con = privacy.grant_consent(s, CTX, "caregiver", cid, ["care matching"])
    assert (con["status"], con["noticeVersion"]) == ("Granted", "1")
    assert con["grantedAt"] and con["withdrawnAt"] is None
    with pytest.raises(DomainError) as e:
        privacy.grant_consent(s, CTX, "caregiver", cid, ["care matching"])
    assert e.value.status == 409
    withdrawn = privacy.withdraw_consent(s, CTX, con["id"])
    assert (withdrawn["status"], bool(withdrawn["withdrawnAt"])) == ("Withdrawn", True)
    with pytest.raises(DomainError) as e:
        privacy.withdraw_consent(s, CTX, con["id"])
    assert e.value.status == 409
    again = privacy.grant_consent(s, CTX, "caregiver", cid, ["visit updates to family"])
    assert again["id"] != con["id"] and again["status"] == "Granted"


@covers("REQ-PRIV-01")
def test_consent_validation():
    s = make()
    for args, status in (
        (("caregiver", "nope", ["care matching"]), 404),
        (("parent", "nope", ["visit updates to family"]), 404),
        (("caregiver", "c1", []), 400),
        (("caregiver", "c1", ["sell the data"]), 400),
        (("stranger", "c1", ["care matching"]), 400),
    ):
        with pytest.raises(DomainError) as e:
            privacy.grant_consent(s, CTX, *args)
        assert e.value.status == status, args
    # The seed already grants consent for c1 and r2, so granting for them is
    # a 409; a fresh request's parent can still be granted.
    with pytest.raises(DomainError) as e:
        privacy.grant_consent(s, CTX, "parent", "r2", ["visit updates to family"])
    assert e.value.status == 409
    fresh = service.create_request(s, CTX, "Nair family", "Mrs. Nair", "Kochi", 2, "Companionship")
    assert privacy.grant_consent(s, CTX, "parent", fresh["id"], ["visit updates to family"])["personType"] == "parent"


@covers("REQ-PRIV-01")
def test_consent_status_is_flagged_in_state():
    s = make()
    state = service.get_state(s)
    assert next(c for c in state["caregivers"] if c["id"] == "c1")["consentStatus"] == "Granted"
    con = next(c for c in s.db["consents"] if c["personId"] == "c1")
    privacy.withdraw_consent(s, CTX, con["id"])
    state = service.get_state(s)
    assert next(c for c in state["caregivers"] if c["id"] == "c1")["consentStatus"] == "Withdrawn"
    new = service.add_caregiver(s, CTX, "Divya", "Pune", DOB)
    assert next(c for c in service.get_state(s)["caregivers"] if c["id"] == new["id"])["consentStatus"] == "None"


@covers("REQ-PRIV-01")
def test_erasure_removes_personal_data_and_keeps_no_personal_data_in_audit():
    s = make()
    c = service.add_caregiver(s, CTX, "Divya Sharma", "Pune", DOB)
    privacy.grant_consent(s, CTX, "caregiver", c["id"], ["care matching"])
    erased = privacy.erase_caregiver(s, CTX, c["id"])
    assert (erased["name"], erased["city"]) == ("[erased]", "[erased]")
    assert "dateOfBirth" not in erased and erased["erased"] is True
    stored = next(x for x in s.db["caregivers"] if x["id"] == c["id"])
    assert (stored["id"], stored["stage"], stored["modules"]) == (c["id"], "Applied", [0, 0, 0, 0, 0])
    assert "dateOfBirth" not in stored
    # The erasure audit event holds placeholders only — never the erased data.
    ev = next(e for e in s.db["audit"] if e["action"] == "caregiver.erased")
    assert "Divya Sharma" not in json.dumps(ev) and "2005-05-05" not in json.dumps(ev)
    assert ev["before"]["name"] == "[erased]" and ev["after"]["erased"] is True
    # Consent is withdrawn as part of the erasure, and the state API shows
    # placeholders only.
    assert privacy.consent_status_for(s.db, "caregiver", c["id"]) == "Withdrawn"
    state_c = next(x for x in service.get_state(s)["caregivers"] if x["id"] == c["id"])
    assert state_c["name"] == "[erased]" and "dateOfBirth" not in state_c
    with pytest.raises(DomainError) as e:
        privacy.erase_caregiver(s, CTX, c["id"])
    assert e.value.status == 409
    assert s.audit.verify()["ok"] is True


@covers("REQ-PRIV-01")
def test_consent_and_erase_api_roles(client):
    # A new care-leaver has no consent; grant, withdraw, erase via the API.
    cid = client.post("/api/caregivers", json={"name": "Divya", "city": "Pune", "dateOfBirth": "2005-05-05"}).json()["id"]
    assert next(c for c in client.get("/api/state").json()["caregivers"] if c["id"] == cid)["consentStatus"] == "None"
    r = client.post("/api/consents", json={"personType": "caregiver", "personId": cid, "purposes": ["care matching"]})
    assert r.status_code == 201, r.text
    assert client.post("/api/consents", json={"personType": "caregiver", "personId": cid, "purposes": ["care matching"]}).status_code == 409
    con_id = r.json()["id"]
    assert client.post(f"/api/consents/{con_id}/withdraw").json()["status"] == "Withdrawn"
    assert next(c for c in client.get("/api/state").json()["caregivers"] if c["id"] == cid)["consentStatus"] == "Withdrawn"
    erased = client.post(f"/api/caregivers/{cid}/erase").json()
    assert erased["name"] == "[erased]" and "dateOfBirth" not in erased
    assert "Divya" not in client.get("/api/audit", params={"action": "caregiver.erased"}).text
    # Non-coordinators cannot grant, withdraw or erase.
    client.post("/api/auth/users", json={"email": "imran@example.com", "role": "caregiver", "password": "care-pass-1", "caregiverId": "c6"})
    as_user(client, "imran@example.com", "care-pass-1")
    assert client.post("/api/consents", json={"personType": "caregiver", "personId": "c6", "purposes": ["care matching"]}).status_code == 403
    assert client.post(f"/api/consents/{con_id}/withdraw").status_code == 403
    assert client.post("/api/caregivers/c6/erase").status_code == 403


# --- REQ-PRIV-02: breach register and the 72-hour clock ---------------------

@covers("REQ-PRIV-02")
def test_breach_clock_is_discovery_plus_72_hours():
    s = make()
    # Relative to now, so the 72-hour window is still open whatever the date.
    discovered = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    b = breach_mod.create_breach(s, CTX, "Lost phone", "A phone with visit notes was lost.", ["c6"], discovered_at=discovered.isoformat())
    assert b["boardDueAt"] == (discovered + timedelta(hours=72)).isoformat()
    assert b["status"] == "Open" and b["overdue"] is False
    assert b["notifiedAt"] is None and b["boardReportedAt"] is None


@covers("REQ-PRIV-02")
def test_breach_overdue_flag_and_notify_then_report():
    s = make()
    old = (datetime.now(timezone.utc) - timedelta(hours=73)).isoformat()
    b = breach_mod.create_breach(s, CTX, "Old breach", "Discovered three days ago.", ["c6"], discovered_at=old)
    view = breach_mod.list_breaches(s)[0]
    assert view["overdue"] is True and view["hoursRemaining"] < 0
    with pytest.raises(DomainError) as e:
        breach_mod.report_breach(s, CTX, b["id"])
    assert e.value.status == 409  # notify first
    notified = breach_mod.notify_breach(s, CTX, b["id"])
    assert (notified["status"], bool(notified["notifiedAt"])) == ("Notified", True)
    with pytest.raises(DomainError) as e:
        breach_mod.notify_breach(s, CTX, b["id"])
    assert e.value.status == 409
    reported = breach_mod.report_breach(s, CTX, b["id"])
    assert (reported["status"], bool(reported["boardReportedAt"])) == ("Reported", True)
    assert breach_mod.list_breaches(s)[0]["overdue"] is False  # reported: clock stops
    with pytest.raises(DomainError) as e:
        breach_mod.report_breach(s, CTX, b["id"])
    assert e.value.status == 409


@covers("REQ-PRIV-02")
def test_breach_validation():
    s = make()
    for args, status in (
        (("", "Something happened.", []), 400),
        (("Title", "", []), 400),
        (("Title", "Something happened.", ["ghost"]), 404),
        (("Title", "Something happened.", [], "not-a-date"), 400),
    ):
        with pytest.raises(DomainError) as e:
            breach_mod.create_breach(s, CTX, *args)
        assert e.value.status == status, args
    with pytest.raises(DomainError) as e:
        breach_mod.notify_breach(s, CTX, "b999")
    assert e.value.status == 404


@covers("REQ-PRIV-02")
def test_breach_api_is_coordinator_only_and_shows_the_countdown(client):
    r = client.post("/api/breaches", json={"title": "Lost phone", "description": "A phone with visit notes was lost.", "affectedPersonIds": ["c6", "r1"]})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "Open" and b["overdue"] is False and b["hoursRemaining"] > 71
    assert client.post(f"/api/breaches/{b['id']}/report").status_code == 409
    assert client.post(f"/api/breaches/{b['id']}/notify").json()["status"] == "Notified"
    assert client.post(f"/api/breaches/{b['id']}/report").json()["status"] == "Reported"
    listed = client.get("/api/breaches").json()
    assert listed[0]["boardDueAt"] and listed[0]["boardReportedAt"]
    # A family cannot read or write the register.
    client.post("/api/auth/users", json={"email": "menon@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"})
    as_user(client, "menon@example.com", "family-pass-1")
    assert client.get("/api/breaches").status_code == 403
    assert client.post("/api/breaches", json={"title": "x", "description": "y"}).status_code == 403
    assert client.post(f"/api/breaches/{b['id']}/notify").status_code == 403
