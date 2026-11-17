"""P1/P2 feature tests: aftercare, EVV, credentials, care plans, notifications,
matching suggestions and reports. Service-level tests use Store() directly;
API tests use TestClient with a coordinator login."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import aftercare, careplans, credentials, evv, matching, notifications, reports, service
from app.domain import Ctx, DomainError
from app.main import create_app
from app.store import Store
from tests._trace import covers

CTX = Ctx("req-1", "t" * 32, "coordinator")
TODAY = date(2026, 11, 16)
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


def make_certified(s, name, city, languages, days, capacity):
    """Drive a new care-leaver through the pipeline to Certified + verified."""
    c = service.add_caregiver(s, CTX, name, city, date(2005, 1, 1))
    service.start_training(s, CTX, c["id"])
    for i in range(5):
        service.toggle_module(s, CTX, c["id"], i)
    service.certify(s, CTX, c["id"])
    for item in ("identity", "background", "references"):
        service.set_verification(s, CTX, c["id"], item, "Verified")
    matching.set_profile(s, CTX, c["id"], languages=languages, availability={"days": days, "hours": "09:00-17:00"}, capacity_visits_per_week=capacity)
    return c


class TestAftercare:
    @covers("REQ-AFT-01")
    def test_aftercare_window_defaults_to_21st_birthday_and_extends_to_23rd(self):
        s = make()
        c1 = next(c for c in service.get_state(s)["caregivers"] if c["id"] == "c1")
        assert c1["aftercareUntil"] == "2027-03-14" and c1["extended"] is False
        c5 = next(c for c in service.get_state(s)["caregivers"] if c["id"] == "c5")
        assert c5["aftercareUntil"] == "2027-09-09" and c5["extended"] is True
        assert c1["institution"] == "Hyderabad Children's Home"
        assert c1["contacts"] == [{"name": "Ms. Rao", "role": "Aftercare officer", "phone": "+91 90000 00001"}]

    @covers("REQ-AFT-01")
    def test_set_aftercare_records_institution_window_contacts_and_consent(self):
        s = make()
        c = aftercare.set_aftercare(s, CTX, "c1", institution="Pune Children's Home", aftercare_until=date(2028, 3, 14), extended=True,
                                    contacts=[{"name": "Mr. Patil", "role": "Child protection officer", "phone": "+91 90000 00002"}], location_consent=True)
        assert (c["institution"], c["aftercareUntil"], c["extended"], c["locationConsent"]) == ("Pune Children's Home", "2028-03-14", True, True)
        assert c["contacts"][0]["role"] == "Child protection officer"
        ev = s.db["audit"][-1]
        assert (ev["action"], ev["spec_refs"]) == ("caregiver.aftercare_set", ["REQ-AFT-01"])
        with pytest.raises(DomainError) as e:
            aftercare.set_aftercare(s, CTX, "c1", contacts=[{"name": "No phone", "role": "Officer"}])
        assert e.value.status == 400

    @covers("REQ-AFT-01")
    def test_aftercare_endpoint_is_coordinator_only(self, client):
        r = client.post("/api/caregivers/c1/aftercare", json={"institution": "Pune Children's Home", "extended": True})
        assert r.status_code == 200 and r.json()["institution"] == "Pune Children's Home"
        client.post("/api/auth/users", json={"email": "fam@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"})
        as_user(client, "fam@example.com", "family-pass-1")
        assert client.post("/api/caregivers/c1/aftercare", json={"institution": "X"}).status_code == 403


class TestAftercareRecords:
    @covers("REQ-AFT-03")
    def test_documents_housing_and_checkins(self):
        s = make()
        c = aftercare.add_document(s, CTX, "c1", "Aadhaar", "XXXX-XXXX-5678", "Verified")
        assert c["documents"] == [{"type": "Aadhaar", "reference": "XXXX-XXXX-5678", "status": "Verified"}]
        c = aftercare.set_housing(s, CTX, "c1", "Stable", "Rented room.")
        assert c["housing"] == {"status": "Stable", "details": "Rented room."}
        c = aftercare.add_checkin(s, CTX, "c1", "Doing well in training.", date(2026, 11, 15))
        assert c["checkins"] == [{"date": "2026-11-15", "note": "Doing well in training."}]
        with pytest.raises(DomainError) as e:
            aftercare.set_housing(s, CTX, "c1", "Palace")
        assert e.value.status == 400

    @covers("REQ-AFT-03")
    def test_family_cannot_see_document_references_housing_or_checkins(self):
        s = make()
        family = {"email": "f@example.com", "role": "family", "caregiverId": None, "requestId": "r1"}
        state = service.get_state(s, family)
        c6 = state["caregivers"][0]
        assert c6["id"] == "c6"
        for key in ("documents", "housing", "checkins", "contacts"):
            assert key not in c6, key
        # The caregiver themself and the coordinator do see them.
        caregiver = {"email": "c@example.com", "role": "caregiver", "caregiverId": "c6", "requestId": None}
        own = service.get_state(s, caregiver)["caregivers"][0]
        assert own["housing"]["status"] == "Stable" and len(own["checkins"]) == 1
        c5 = next(c for c in service.get_state(s)["caregivers"] if c["id"] == "c5")
        assert c5["documents"] == [{"type": "Aadhaar", "reference": "XXXX-XXXX-1234", "status": "Verified"}]

    @covers("REQ-AFT-03")
    def test_caregiver_may_add_their_own_checkin(self, client):
        client.post("/api/auth/users", json={"email": "imran@example.com", "role": "caregiver", "password": "care-pass-1", "caregiverId": "c6"})
        as_user(client, "imran@example.com", "care-pass-1")
        r = client.post("/api/caregivers/c6/checkins", json={"note": "Feeling settled."})
        assert r.status_code == 201
        assert client.post("/api/caregivers/c1/checkins", json={"note": "Not mine."}).status_code == 403


class TestVisitVerification:
    @covers("REQ-EVV-01")
    def test_checkin_and_checkout_record_timestamps(self):
        s = make()
        v = service.log_visit(s, CTX, "r1", "Good", "Tea and a walk.", planned_at="2026-11-16T10:00:00+00:00")
        out = evv.check_in(s, CTX, v["id"], timestamp="2026-11-16T10:05:00+00:00")
        assert out["checkinAt"] == "2026-11-16T10:05:00+00:00" and "checkoutAt" not in out
        out = evv.check_out(s, CTX, v["id"], timestamp="2026-11-16T11:00:00+00:00")
        assert out["checkoutAt"] == "2026-11-16T11:00:00+00:00"
        assert s.db["audit"][-1]["spec_refs"] == ["REQ-EVV-01"]

    @covers("REQ-EVV-01")
    def test_location_without_consent_is_rejected_and_nothing_is_stored(self):
        s = make()
        aftercare.set_aftercare(s, CTX, "c6", location_consent=False)
        v = service.log_visit(s, CTX, "r1", "Good", "Tea.", planned_at="2026-11-16T10:00:00+00:00")
        with pytest.raises(DomainError) as e:
            evv.check_in(s, CTX, v["id"], timestamp="2026-11-16T10:00:00+00:00", location="17.4,78.4")
        assert e.value.status == 400
        stored = next(x for x in s.db["visits"] if x["id"] == v["id"])
        assert "checkinAt" not in stored and "checkinLocation" not in stored
        # With consent the location is stored.
        aftercare.set_aftercare(s, CTX, "c6", location_consent=True)
        out = evv.check_in(s, CTX, v["id"], timestamp="2026-11-16T10:00:00+00:00", location="17.4,78.4")
        assert out["checkinLocation"] == "17.4,78.4"

    @covers("REQ-EVV-01")
    def test_late_checkin_notifies_coordinator_and_family(self):
        s = make()
        v = service.log_visit(s, CTX, "r1", "Good", "Tea.", planned_at="2026-11-16T10:00:00+00:00")
        evv.check_in(s, CTX, v["id"], timestamp="2026-11-16T10:31:00+00:00")
        late = [n for n in s.db["notifications"] if n["kind"] == "late"]
        assert len(late) == 2
        assert {n["audience"]["role"] for n in late} == {"coordinator", "family"}
        assert late[0]["audience"].get("requestId") in (None, "r1")
        # On time (within 30 minutes) creates no notification.
        v2 = service.log_visit(s, CTX, "r1", "Good", "Walk.", planned_at="2026-11-16T12:00:00+00:00")
        evv.check_in(s, CTX, v2["id"], timestamp="2026-11-16T12:29:00+00:00")
        assert len([n for n in s.db["notifications"] if n["kind"] == "late"]) == 2

    @covers("REQ-EVV-01")
    def test_checkout_before_checkin_and_double_checkin_are_rejected(self):
        s = make()
        v = service.log_visit(s, CTX, "r1", "Good", "Tea.")
        with pytest.raises(DomainError) as e:
            evv.check_out(s, CTX, v["id"])
        assert e.value.status == 409
        evv.check_in(s, CTX, v["id"])
        with pytest.raises(DomainError) as e:
            evv.check_in(s, CTX, v["id"])
        assert e.value.status == 409

    @covers("REQ-EVV-01")
    def test_checkin_endpoint_enforces_ownership(self, client):
        v = client.post("/api/visits", json={"requestId": "r1", "mood": "Good", "note": "Tea.", "plannedAt": "2026-11-16T10:00:00+00:00"}).json()
        r = client.post(f"/api/visits/{v['id']}/checkin", json={"timestamp": "2026-11-16T10:05:00+00:00", "location": "17.4,78.4"})
        assert r.status_code == 200 and r.json()["checkinLocation"] == "17.4,78.4"
        assert client.post(f"/api/visits/{v['id']}/checkout", json={"timestamp": "2026-11-16T11:00:00+00:00"}).status_code == 200
        client.post("/api/auth/users", json={"email": "fam@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"})
        as_user(client, "fam@example.com", "family-pass-1")
        assert client.post(f"/api/visits/{v['id']}/checkin", json={}).status_code == 403


class TestCredentials:
    @covers("REQ-CRED-01")
    def test_expiring_list_is_soonest_first_and_flags_stale_verification(self):
        s = make()
        expiring = credentials.expiring_credentials(s, days=30, today=TODAY)
        # c6 background check expired 2026-11-01; c5 background check expires 2026-12-01.
        assert [e["credential"]["id"] for e in expiring] == ["k3", "k1"]
        assert expiring[0]["daysLeft"] == -15 and expiring[0]["verificationStale"] is True
        assert expiring[1]["daysLeft"] == 15 and expiring[1]["verificationStale"] is False
        # A credential expiring beyond the window is not listed.
        assert all(e["credential"]["id"] != "k4" for e in expiring)
        # The flag is in get_state too, and the verification record is untouched.
        by_id = {c["id"]: c for c in service.get_state(s)["caregivers"]}
        assert by_id["c6"]["verificationStale"] is True and by_id["c5"]["verificationStale"] is False
        assert by_id["c6"]["verification"]["background"]["status"] == "Verified"

    @covers("REQ-CRED-01")
    def test_add_credential_computes_status_from_expiry(self):
        s = make()
        c = credentials.add_credential(s, CTX, "c1", "First aid", date(2027, 1, 1), date(2026, 1, 1), today=TODAY)
        assert c["credentials"][-1]["status"] == "Valid"
        c = credentials.add_credential(s, CTX, "c1", "Background check", date(2026, 11, 1), today=TODAY)
        assert c["credentials"][-1]["status"] == "Expired"
        with pytest.raises(DomainError) as e:
            credentials.add_credential(s, CTX, "c1", "First aid", date(2026, 1, 1), date(2026, 6, 1), today=TODAY)
        assert e.value.status == 400

    @covers("REQ-CRED-01")
    def test_expiring_endpoint(self, client):
        r = client.get("/api/credentials/expiring", params={"days": 30})
        assert r.status_code == 200
        assert [e["credential"]["id"] for e in r.json()["expiring"]] == ["k3", "k1"]
        assert client.post("/api/caregivers/c1/credentials", json={"type": "First aid", "issuedDate": "2026-01-01", "expiryDate": "2027-01-01"}).status_code == 201


class TestCarePlans:
    @covers("REQ-PLAN-01")
    def test_care_plan_crud_and_task_toggle(self):
        s = make()
        plan = careplans.set_care_plan(s, CTX, "r2", "Escort to medical appointments.")
        assert plan["goals"] == "Escort to medical appointments." and plan["tasks"] == []
        task = careplans.add_task(s, CTX, "r2", "Accompany to the clinic")
        assert task["done"] is False
        task = careplans.toggle_task(s, CTX, "r2", task["id"])
        assert task["done"] is True
        assert careplans.get_care_plan(s, "r2")["tasks"][0]["done"] is True
        with pytest.raises(DomainError) as e:
            careplans.toggle_task(s, CTX, "r2", "nope")
        assert e.value.status == 404

    @covers("REQ-PLAN-01")
    def test_matched_caregiver_and_family_see_the_plan(self):
        s = make()
        caregiver = {"email": "c@example.com", "role": "caregiver", "caregiverId": "c6", "requestId": None}
        state = service.get_state(s, caregiver)
        assert state["requests"][0]["carePlan"]["goals"].startswith("Daily companionship")
        assert state["requests"][0]["carePlan"]["tasks"][0]["title"] == "Morning walk in the garden"
        family = {"email": "f@example.com", "role": "family", "caregiverId": None, "requestId": "r1"}
        assert service.get_state(s, family)["requests"][0]["carePlan"]["tasks"][2]["title"] == "Help with lunch"

    @covers("REQ-PLAN-01")
    def test_care_plan_endpoints(self, client):
        assert client.get("/api/requests/r1/care-plan").json()["tasks"][0]["done"] is True
        assert client.post("/api/requests/r2/care-plan", json={"goals": "Escort to appointments."}).status_code == 200
        task = client.post("/api/requests/r2/care-plan/tasks", json={"title": "Accompany to the clinic"}).json()
        toggled = client.post(f"/api/requests/r2/care-plan/tasks/{task['id']}/toggle").json()
        assert toggled["done"] is True
        client.post("/api/auth/users", json={"email": "imran@example.com", "role": "caregiver", "password": "care-pass-1", "caregiverId": "c6"})
        as_user(client, "imran@example.com", "care-pass-1")
        assert client.get("/api/requests/r1/care-plan").json()["goals"].startswith("Daily companionship")
        assert client.get("/api/requests/r2/care-plan").status_code == 403


class TestNotifications:
    @covers("REQ-NOTIF-01")
    def test_needs_attention_notifies_coordinator_and_family_only(self):
        s = make()
        flagged = service.log_visit(s, CTX, "r1", "Needs attention", "Missed medication.")
        created = notifications.notify_for_visit(s, CTX, flagged)
        assert len(created) == 2
        assert {n["audience"]["role"] for n in created} == {"coordinator", "family"}
        assert created[1]["audience"]["requestId"] == "r1"
        assert all(n["kind"] == "needs_attention" for n in created)
        good = service.log_visit(s, CTX, "r1", "Good", "All well.")
        assert notifications.notify_for_visit(s, CTX, good) == []

    @covers("REQ-NOTIF-01")
    def test_missed_visits_dedupe_until_read(self):
        s = make()
        # Seed r1: last visit 2026-11-12, 2 visits a week -> overdue on 2026-11-16.
        created = notifications.check_missed_visits(s, CTX, today=TODAY)
        assert len(created) == 2 and all(n["kind"] == "missed" for n in created)
        assert notifications.check_missed_visits(s, CTX, today=TODAY) == []
        # Once both are read, the next check flags it again.
        for n in created:
            notifications.mark_read(s, CTX, n["id"], {"email": "coordinator@saathi.local", "role": "coordinator"})
        assert len(notifications.check_missed_visits(s, CTX, today=TODAY)) == 2

    @covers("REQ-NOTIF-01")
    def test_notifications_are_role_scoped_and_markable_read(self, client):
        v = client.post("/api/visits", json={"requestId": "r1", "mood": "Needs attention", "note": "Missed medication."}).json()
        # The visit route wires notify_for_visit in after log_visit (merge step),
        # so the two notifications already exist without calling the helper.
        mine = client.get("/api/notifications").json()
        assert len(mine) == 2
        coord_id = next(n["id"] for n in mine if n["audience"]["role"] == "coordinator")
        assert client.post(f"/api/notifications/{coord_id}/read").json()["readAt"] is not None
        client.post("/api/auth/users", json={"email": "menon@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"})
        as_user(client, "menon@example.com", "family-pass-1")
        family_notes = client.get("/api/notifications").json()
        assert len(family_notes) == 1 and family_notes[0]["audience"]["role"] == "family"
        # The family cannot mark the coordinator's notification read.
        assert client.post(f"/api/notifications/{coord_id}/read").status_code == 403
        assert client.post("/api/notifications/check-missed").status_code == 403


class TestMatching:
    @covers("REQ-MATCH-02")
    def test_suggestions_rank_city_and_language_first(self):
        s = make()
        best = make_certified(s, "Asha", "Hyderabad", ["Hindi", "English"], ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], 5)
        make_certified(s, "Kumar", "Chennai", ["Tamil"], ["Monday", "Tuesday"], 2)
        result = matching.suggest_caregivers(s, "r2")  # Hyderabad, Hindi, 3 visits a week.
        ranked = [x["caregiverId"] for x in result["suggestions"]]
        assert ranked[0] == best["id"]  # City + language match beats the Chennai candidate.
        assert ranked[1] == "c5"  # Seed Hyderabad candidate, also Hindi-speaking.
        assert "c4" not in ranked  # Certified but references still Pending: not cleared.
        top = result["suggestions"][0]
        assert top["score"] == 100 and top["breakdown"]["city"]["matched"] is True
        assert top["breakdown"]["language"]["matched"] is True
        chennai = next(x for x in result["suggestions"] if x["name"] == "Kumar")
        assert chennai["breakdown"]["city"]["points"] == 0 and chennai["breakdown"]["language"]["points"] == 0

    @covers("REQ-MATCH-02")
    def test_request_language_is_stored_and_suggestions_endpoint(self, client):
        rid = client.post("/api/requests", json={"family": "Nair", "parent": "Mrs. Nair", "city": "Hyderabad", "visitsPerWeek": 2, "need": "Companionship", "language": "Hindi"}).json()["id"]
        assert client.get("/api/state").json()["requests"][-1]["language"] == "Hindi"
        result = client.get(f"/api/requests/{rid}/suggestions").json()
        assert result["suggestions"][0]["caregiverId"] == "c5"
        assert result["suggestions"][0]["breakdown"]["city"]["matched"] is True


class TestReports:
    @covers("REQ-REP-01")
    def test_summary_numbers_on_seed_data(self):
        s = make()
        # r1 matched 2026-11-10: by 2026-11-24 that is 2 full weeks x 2 visits = 4 expected, 1 logged.
        out = reports.summary(s, today=date(2026, 11, 24))
        assert out["visits"] == {"logged": 1, "expected": 4, "completionRate": 0.25}
        assert out["punctuality"]["withCheckInAndCheckOut"] == 1 and out["punctuality"]["checkInOutRate"] == 1.0
        assert out["punctuality"]["late"] == 0 and out["punctuality"]["lateRate"] == 0.0
        assert out["flaggedVisits"] == 0
        assert out["retention"] == {"everMatched": 1, "currentlyMatched": 1, "rate": 1.0}
        assert out["placement"] == {"matchedRequests": 1, "totalRequests": 3, "rate": 1 / 3}

    @covers("REQ-REP-01")
    def test_summary_counts_flagged_and_late_visits(self):
        s = make()
        service.log_visit(s, CTX, "r1", "Needs attention", "Missed medication.", planned_at="2026-11-16T10:00:00+00:00")
        v = s.db["visits"][0]
        evv.check_in(s, CTX, v["id"], timestamp="2026-11-16T10:45:00+00:00")
        out = reports.summary(s, today=TODAY)
        assert out["flaggedVisits"] == 1
        assert out["punctuality"]["late"] == 1 and out["punctuality"]["lateRate"] == 0.5
        assert out["punctuality"]["withCheckInAndCheckOut"] == 1  # Only the seed visit has both.

    @covers("REQ-REP-01")
    def test_reports_endpoint_is_coordinator_only(self, client):
        r = client.get("/api/reports/summary")
        assert r.status_code == 200 and r.json()["placement"]["rate"] == 1 / 3
        client.post("/api/auth/users", json={"email": "fam@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"})
        as_user(client, "fam@example.com", "family-pass-1")
        assert client.get("/api/reports/summary").status_code == 403
