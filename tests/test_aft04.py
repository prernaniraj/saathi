"""Enforce the 23-year aftercare cap (REQ-AFT-04).

Service-level tests use Store() directly; API tests use TestClient + login,
following tests/test_api.py. Dates are pinned explicitly wherever the
boundary matters, so these tests do not depend on the day they run.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import aftercare, matching, service
from app.domain import Ctx, DomainError
from app.main import create_app
from app.store import Store
from tests._trace import covers

CTX = Ctx("req-1", "t" * 32, "coordinator@saathi.local")
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


def drive_to_certified(s, dob, name="Asha", city="Pune"):
    """A new care-leaver driven to Certified + verified, but not matched."""
    c = service.add_caregiver(s, CTX, name, city, dob)
    service.start_training(s, CTX, c["id"])
    for i in range(5):
        service.toggle_module(s, CTX, c["id"], i)
    service.certify(s, CTX, c["id"])
    for item in ("identity", "background", "references"):
        service.set_verification(s, CTX, c["id"], item, "Verified")
    return c


@covers("REQ-AFT-04")
def test_twenty_third_birthday():
    assert aftercare.twenty_third_birthday("2005-05-05") == "2028-05-05"
    assert aftercare.twenty_third_birthday("2004-02-29") == "2027-02-28"
    assert aftercare.twenty_third_birthday("not-a-date") is None
    assert aftercare.twenty_third_birthday(None) is None


@covers("REQ-AFT-04")
def test_aftercare_expired_boundary_on_the_21st_birthday():
    s = make()
    c = service.add_caregiver(s, CTX, "Asha", "Pune", date(2005, 11, 2))
    assert aftercare.aftercare_end(c) == date(2026, 11, 2)
    assert aftercare.aftercare_expired(c, date(2026, 11, 1)) is False
    # The 21st birthday itself is already expired, not the day after.
    assert aftercare.aftercare_expired(c, date(2026, 11, 2)) is True
    assert aftercare.aftercare_expired(c, date(2026, 11, 3)) is True


@covers("REQ-AFT-04")
def test_aftercare_expired_boundary_on_the_23rd_birthday_when_extended():
    s = make()
    c = service.add_caregiver(s, CTX, "Asha", "Pune", date(2005, 11, 2))
    aftercare.set_aftercare(s, CTX, c["id"], extended=True)
    stored = next(x for x in s.db["caregivers"] if x["id"] == c["id"])
    assert aftercare.aftercare_end(stored) == date(2028, 11, 2)
    assert aftercare.aftercare_expired(stored, date(2028, 11, 1)) is False
    assert aftercare.aftercare_expired(stored, date(2028, 11, 2)) is True


@covers("REQ-AFT-04")
def test_aftercare_expired_uses_a_recorded_until_date():
    s = make()
    c = service.add_caregiver(s, CTX, "Asha", "Pune", date(2005, 5, 5))
    aftercare.set_aftercare(s, CTX, c["id"], aftercare_until=date(2027, 6, 1))
    stored = next(x for x in s.db["caregivers"] if x["id"] == c["id"])
    assert aftercare.aftercare_end(stored) == date(2027, 6, 1)
    assert aftercare.aftercare_expired(stored, date(2027, 5, 31)) is False
    assert aftercare.aftercare_expired(stored, date(2027, 6, 1)) is True


@covers("REQ-AFT-04")
def test_set_aftercare_extended_moves_the_window_to_the_23rd_birthday():
    s = make()
    c = service.add_caregiver(s, CTX, "Asha", "Pune", date(2005, 5, 5))
    assert c["aftercareUntil"] == "2026-05-05"
    aftercare.set_aftercare(s, CTX, c["id"], extended=True)
    stored = next(x for x in s.db["caregivers"] if x["id"] == c["id"])
    assert stored["aftercareUntil"] == "2028-05-05"
    assert stored["extended"] is True
    # An explicit date still wins over the computed 23rd birthday.
    aftercare.set_aftercare(s, CTX, c["id"], aftercare_until=date(2029, 1, 15), extended=True)
    stored = next(x for x in s.db["caregivers"] if x["id"] == c["id"])
    assert stored["aftercareUntil"] == "2029-01-15"


@covers("REQ-AFT-04")
def test_match_refuses_an_expired_care_leaver_but_their_data_still_reads():
    s = make()
    c = drive_to_certified(s, date(2005, 11, 2))
    r = service.create_request(s, CTX, "Nair family", "Mrs. Nair", "Pune", 2, "Companionship")
    # The day before the 21st birthday the match still works.
    assert service.match(s, CTX, r["id"], c["id"], today=date(2026, 11, 1))["status"] == "Matched"
    # A second, identical setup is refused on the 21st birthday itself.
    s2 = make()
    c2 = drive_to_certified(s2, date(2005, 11, 2))
    r2 = service.create_request(s2, CTX, "Nair family", "Mrs. Nair", "Pune", 2, "Companionship")
    with pytest.raises(DomainError) as e:
        service.match(s2, CTX, r2["id"], c2["id"], today=date(2026, 11, 2))
    assert e.value.status == 409
    assert "aftercare window has ended" in e.value.message
    # The refusal changes nothing: the request is still Open, the
    # care-leaver is still Certified, and their record still reads.
    stored_r = next(x for x in s2.db["requests"] if x["id"] == r2["id"])
    assert (stored_r["status"], stored_r["caregiverId"]) == ("Open", None)
    stored_c = next(x for x in s2.db["caregivers"] if x["id"] == c2["id"])
    assert stored_c["stage"] == "Certified"
    state = service.get_state(s2)
    assert any(x["id"] == c2["id"] for x in state["caregivers"])


@covers("REQ-AFT-04")
def test_match_refuses_after_the_23rd_birthday_when_extended():
    s = make()
    c = drive_to_certified(s, date(2005, 11, 2))
    aftercare.set_aftercare(s, CTX, c["id"], extended=True)
    r = service.create_request(s, CTX, "Nair family", "Mrs. Nair", "Pune", 2, "Companionship")
    assert service.match(s, CTX, r["id"], c["id"], today=date(2028, 11, 1))["status"] == "Matched"
    s2 = make()
    c2 = drive_to_certified(s2, date(2005, 11, 2))
    aftercare.set_aftercare(s2, CTX, c2["id"], extended=True)
    r2 = service.create_request(s2, CTX, "Nair family", "Mrs. Nair", "Pune", 2, "Companionship")
    with pytest.raises(DomainError) as e:
        service.match(s2, CTX, r2["id"], c2["id"], today=date(2028, 11, 2))
    assert e.value.status == 409


@covers("REQ-AFT-04")
def test_training_and_certification_are_not_blocked_after_expiry():
    # Scope decision (SPEC section 8): only matching is blocked. A
    # care-leaver whose window has ended can still train and certify.
    s = make()
    c = service.add_caregiver(s, CTX, "Asha", "Pune", date(2005, 5, 5))
    assert aftercare.aftercare_expired(c) is True
    service.start_training(s, CTX, c["id"])
    for i in range(5):
        service.toggle_module(s, CTX, c["id"], i)
    assert service.certify(s, CTX, c["id"])["stage"] == "Certified"


@covers("REQ-AFT-04")
def test_state_surfaces_aftercare_expired():
    s = make()
    state = service.get_state(s)
    by_id = {c["id"]: c for c in state["caregivers"]}
    # Seed c4 (born 2006-11-02) and c5 (extended to 2027-09-09) are inside
    # their windows; seed c2 (born 2005-07-21, still Training) is past his.
    assert by_id["c4"]["aftercareExpired"] is False
    assert by_id["c5"]["aftercareExpired"] is False
    assert by_id["c2"]["aftercareExpired"] is True
    fresh = service.add_caregiver(s, CTX, "Asha", "Pune", date(2005, 5, 5))
    state = service.get_state(s)
    assert next(c for c in state["caregivers"] if c["id"] == fresh["id"])["aftercareExpired"] is True


@covers("REQ-AFT-04")
def test_suggestions_still_include_expired_care_leavers():
    # Scope decision (SPEC section 8): suggestions are unchanged; the 409
    # only fires when the coordinator actually tries to match.
    s = make()
    c = drive_to_certified(s, date(2005, 5, 5))
    matching.set_profile(s, CTX, c["id"], languages=["Hindi"], availability={"days": ["Monday", "Tuesday", "Wednesday"], "hours": "09:00-17:00"}, capacity_visits_per_week=3)
    r = service.create_request(s, CTX, "Nair family", "Mrs. Nair", "Pune", 2, "Companionship")
    ranked = [x["caregiverId"] for x in matching.suggest_caregivers(s, r["id"])["suggestions"]]
    assert c["id"] in ranked


@covers("REQ-AFT-04")
def test_match_refusal_through_the_api(client):
    cid = client.post("/api/caregivers", json={"name": "Asha", "city": "Pune", "dateOfBirth": "2005-05-05"}).json()["id"]
    client.post(f"/api/caregivers/{cid}/start")
    for i in range(5):
        client.post(f"/api/caregivers/{cid}/modules/{i}")
    client.post(f"/api/caregivers/{cid}/certify")
    for item in ("identity", "background", "references"):
        client.post(f"/api/caregivers/{cid}/verification/{item}", json={"status": "Verified"})
    rid = client.post("/api/requests", json={"family": "Nair", "parent": "Mrs. Nair", "city": "Pune", "visitsPerWeek": "2", "need": "Companionship"}).json()["id"]
    r = client.post(f"/api/requests/{rid}/match", json={"caregiverId": cid})
    assert r.status_code == 409, r.text
    assert "aftercare window has ended" in r.json()["error"]
    state = client.get("/api/state").json()
    assert next(c for c in state["caregivers"] if c["id"] == cid)["aftercareExpired"] is True
    assert next(x for x in state["requests"] if x["id"] == rid)["status"] == "Open"
