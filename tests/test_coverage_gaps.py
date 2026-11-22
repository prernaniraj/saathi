"""Coverage-gap tests: error paths, role branches and helper variants added in
the remaining-features build that the first test pass never exercised.

Each test is marked @covers(...) for the requirement it serves, following
SPEC.md section 1. Service-level tests use Store() directly; API tests use
TestClient with a coordinator login, following tests/test_api.py.
"""
import io
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import aftercare, auth, breach as breach_mod, careplans, credentials, evv, incidents, matching, notifications, observability, privacy, reports, service
from app.domain import Ctx, DomainError
from app.main import create_app
from app.store import Store
from tests._trace import covers

CTX = Ctx("req-1", "t" * 32, "coordinator@saathi.local")
DOB = date(2005, 5, 5)
TODAY = date(2026, 11, 16)
SEED_PASSWORD = "test-coordinator-password"
SEED_EMAIL = "coordinator@saathi.local"
ROOT = Path(__file__).resolve().parent.parent


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


def drive_to_matched(s, name="Asha", city="Hyderabad"):
    """A new care-leaver driven to Certified + verified, matched to a fresh request."""
    c = service.add_caregiver(s, CTX, name, city, date(2007, 1, 1))
    service.start_training(s, CTX, c["id"])
    for i in range(5):
        service.toggle_module(s, CTX, c["id"], i)
    service.certify(s, CTX, c["id"])
    for item in ("identity", "background", "references"):
        service.set_verification(s, CTX, c["id"], item, "Verified")
    r = service.create_request(s, CTX, "Test family", "Mr. Test", city, 2, "Companionship")
    service.match(s, CTX, r["id"], c["id"])
    return c, r


class TestAuthHelpers:
    @covers("REQ-RBAC-01")
    def test_verify_password_with_unusable_salt_returns_false(self):
        assert auth.verify_password("pw", "zz", "aa") is False  # odd-length hex -> ValueError
        assert auth.verify_password("pw", 123, "aa") is False  # not a string -> TypeError

    @covers("REQ-RBAC-01")
    def test_ensure_seed_coordinator_returns_none_when_it_exists(self):
        assert auth.ensure_seed_coordinator(make().db) is None

    @covers("REQ-RBAC-01")
    def test_expired_session_does_not_authenticate(self):
        s = make()
        token = auth.create_session(s.db, SEED_EMAIL)
        assert auth.authenticate(s.db, token)["email"] == SEED_EMAIL
        s.db["sessions"][0]["expires"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert auth.authenticate(s.db, token) is None
        assert auth.authenticate(s.db, "") is None

    @covers("REQ-RBAC-01")
    def test_revoke_session_with_no_or_unknown_token(self):
        s = make()
        assert auth.revoke_session(s.db, "") is False
        assert auth.revoke_session(s.db, "no-such-token") is False
        token = auth.create_session(s.db, SEED_EMAIL)
        assert auth.revoke_session(s.db, token) is True
        assert auth.revoke_session(s.db, token) is False


class TestAftercareHelpers:
    @covers("REQ-AFT-01")
    def test_as_date_accepts_datetime_and_rejects_garbage(self):
        s = make()
        c = aftercare.set_aftercare(s, CTX, "c1", aftercare_until=datetime(2028, 3, 14, 10, 0))
        assert c["aftercareUntil"] == "2028-03-14"
        with pytest.raises(DomainError) as e:
            aftercare.set_aftercare(s, CTX, "c1", aftercare_until="not-a-date")
        assert e.value.status == 400

    @covers("REQ-AFT-01")
    def test_aftercare_for_unknown_caregiver_is_404(self):
        with pytest.raises(DomainError) as e:
            aftercare.set_aftercare(make(), CTX, "nope", institution="X")
        assert e.value.status == 404

    @covers("REQ-AFT-01")
    def test_21st_birthday_fallbacks(self):
        assert aftercare.twenty_first_birthday("not-a-date") is None
        assert aftercare.twenty_first_birthday(None) is None
        assert aftercare.twenty_first_birthday("2004-02-29") == "2025-02-28"
        assert aftercare.twenty_first_birthday("2005-05-05") == "2026-05-05"

    @covers("REQ-AFT-01")
    def test_set_aftercare_rejects_empty_institution_and_non_list_contacts(self):
        s = make()
        for kwargs in ({"institution": "   "}, {"contacts": "not-a-list"}):
            with pytest.raises(DomainError) as e:
                aftercare.set_aftercare(s, CTX, "c1", **kwargs)
            assert e.value.status == 400

    @covers("REQ-AFT-03")
    def test_document_and_checkin_validation(self):
        s = make()
        for args, status in (
            (("c1", "", "REF-1", "Pending"), 400),
            (("c1", "Aadhaar", "", "Pending"), 400),
            (("c1", "Aadhaar", "REF-1", "Maybe"), 400),
        ):
            with pytest.raises(DomainError) as e:
                aftercare.add_document(s, CTX, *args)
            assert e.value.status == status, args
        with pytest.raises(DomainError) as e:
            aftercare.add_checkin(s, CTX, "c1", "   ")
        assert e.value.status == 400


class TestBreachHelpers:
    @covers("REQ-PRIV-02")
    def test_naive_discovered_time_is_treated_as_utc(self):
        s = make()
        b = breach_mod.create_breach(s, CTX, "Lost phone", "Notes lost.", ["c6"], discovered_at="2026-11-16T10:00:00")
        assert b["boardDueAt"] == "2026-11-19T10:00:00+00:00"

    @covers("REQ-PRIV-02")
    def test_find_skips_non_matching_breaches(self):
        s = make()
        breach_mod.create_breach(s, CTX, "One", "First.", ["c6"])
        with pytest.raises(DomainError) as e:
            breach_mod.notify_breach(s, CTX, "b999")
        assert e.value.status == 404


class TestCarePlanHelpers:
    @covers("REQ-PLAN-01")
    def test_unknown_request_is_404_and_missing_plan_is_none(self):
        s = make()
        with pytest.raises(DomainError) as e:
            careplans.get_care_plan(s, "nope")
        assert e.value.status == 404
        assert careplans.get_care_plan(s, "r2") is None
        for call in (lambda: careplans.set_care_plan(s, CTX, "nope", "x"),
                     lambda: careplans.add_task(s, CTX, "nope", "x"),
                     lambda: careplans.toggle_task(s, CTX, "nope", "t1")):
            with pytest.raises(DomainError) as e:
                call()
            assert e.value.status == 404

    @covers("REQ-PLAN-01")
    def test_empty_goals_title_and_missing_plan(self):
        s = make()
        with pytest.raises(DomainError) as e:
            careplans.set_care_plan(s, CTX, "r2", "   ")
        assert e.value.status == 400
        with pytest.raises(DomainError) as e:
            careplans.add_task(s, CTX, "r2", "")
        assert e.value.status == 400
        with pytest.raises(DomainError) as e:
            careplans.toggle_task(s, CTX, "r2", "t1")
        assert e.value.status == 404  # no plan at all


class TestCredentialHelpers:
    @covers("REQ-CRED-01")
    def test_as_date_accepts_datetime_and_rejects_garbage(self):
        s = make()
        c = credentials.add_credential(s, CTX, "c1", "First aid", datetime(2027, 1, 1, 9, 0), today=TODAY)
        assert c["credentials"][-1]["expiryDate"] == "2027-01-01"
        with pytest.raises(DomainError) as e:
            credentials.add_credential(s, CTX, "c1", "First aid", "not-a-date", today=TODAY)
        assert e.value.status == 400

    @covers("REQ-CRED-01")
    def test_unknown_caregiver_and_empty_type(self):
        s = make()
        with pytest.raises(DomainError) as e:
            credentials.add_credential(s, CTX, "nope", "First aid", date(2027, 1, 1), today=TODAY)
        assert e.value.status == 404
        with pytest.raises(DomainError) as e:
            credentials.add_credential(s, CTX, "c1", "  ", date(2027, 1, 1), today=TODAY)
        assert e.value.status == 400

    @covers("REQ-CRED-01")
    def test_corrupt_credential_dates_are_skipped_not_crash(self):
        s = make()
        c1 = next(c for c in s.db["caregivers"] if c["id"] == "c1")
        c1["credentials"] = [
            {"id": "kx", "type": "Background check", "expiryDate": "not-a-date", "status": "Valid"},
            {"id": "ky", "type": "Background check", "status": "Valid"},  # no expiryDate at all
        ]
        assert credentials.verification_stale(c1, TODAY) is False
        assert [e["credential"]["id"] for e in credentials.expiring_credentials(s, days=30, today=TODAY)] == ["k3", "k1"]


class TestVisitVerificationHelpers:
    @covers("REQ-EVV-01")
    def test_parse_dt_forms(self):
        s = make()
        v = service.log_visit(s, CTX, "r1", "Good", "Tea.", planned_at="2026-11-16T10:00:00+00:00")
        out = evv.check_in(s, CTX, v["id"], timestamp=datetime(2026, 11, 16, 10, 5, tzinfo=timezone.utc))
        assert out["checkinAt"] == "2026-11-16T10:05:00+00:00"
        v2 = service.log_visit(s, CTX, "r1", "Good", "Walk.", planned_at="2026-11-16T12:00:00+00:00")
        out = evv.check_in(s, CTX, v2["id"], timestamp=date(2026, 11, 16))
        assert out["checkinAt"] == "2026-11-16T00:00:00+00:00"
        v3 = service.log_visit(s, CTX, "r1", "Good", "Lunch.", planned_at="2026-11-16T13:00:00+00:00")
        out = evv.check_in(s, CTX, v3["id"], timestamp="2026-11-16T13:05:00")
        assert out["checkinAt"] == "2026-11-16T13:05:00+00:00"
        v4 = service.log_visit(s, CTX, "r1", "Good", "Tea again.", planned_at="2026-11-16T14:00:00+00:00")
        with pytest.raises(DomainError) as e:
            evv.check_in(s, CTX, v4["id"], timestamp="not-a-time")
        assert e.value.status == 400

    @covers("REQ-EVV-01")
    def test_is_late_needs_both_times(self):
        assert evv.is_late({}) is False
        assert evv.is_late({"plannedAt": "2026-11-16T10:00:00+00:00"}) is False
        assert evv.is_late({"checkinAt": "2026-11-16T10:00:00+00:00"}) is False
        assert evv.is_late({"plannedAt": "2026-11-16T10:00:00+00:00", "checkinAt": "2026-11-16T10:30:00+00:00"}) is False
        assert evv.is_late({"plannedAt": "2026-11-16T10:00:00+00:00", "checkinAt": "2026-11-16T10:31:00+00:00"}) is True

    @covers("REQ-EVV-01")
    def test_unknown_visit_and_caregiver_are_404(self):
        s = make()
        with pytest.raises(DomainError) as e:
            evv.check_in(s, CTX, "v999")
        assert e.value.status == 404
        v = service.log_visit(s, CTX, "r1", "Good", "Tea.")
        # log_visit returns a copy, and visits are stored newest-first:
        # corrupt the stored visit by id, not the copy or the last row.
        stored = next(x for x in s.db["visits"] if x["id"] == v["id"])
        stored["caregiverId"] = "ghost"
        with pytest.raises(DomainError) as e:
            evv.check_in(s, CTX, v["id"])
        assert e.value.status == 404

    @covers("REQ-EVV-01")
    def test_double_checkout_and_checkout_location(self):
        s = make()
        v = service.log_visit(s, CTX, "r1", "Good", "Tea.", planned_at="2026-11-16T10:00:00+00:00")
        evv.check_in(s, CTX, v["id"], timestamp="2026-11-16T10:05:00+00:00")
        out = evv.check_out(s, CTX, v["id"], timestamp="2026-11-16T11:00:00+00:00", location="17.4,78.4")
        assert out["checkoutLocation"] == "17.4,78.4"
        with pytest.raises(DomainError) as e:
            evv.check_out(s, CTX, v["id"])
        assert e.value.status == 409

    @covers("REQ-EVV-01")
    def test_late_notification_without_a_matching_request(self):
        s = make()
        # A visit whose request no longer exists: only the coordinator is notified.
        ghost = {"id": "v900", "requestId": "ghost", "caregiverId": "c6", "date": "2026-11-16",
                 "mood": "Good", "note": "Orphan visit.", "plannedAt": "2026-11-16T10:00:00+00:00"}
        s.db["visits"].append(ghost)
        evv.check_in(s, CTX, "v900", timestamp="2026-11-16T11:00:00+00:00")
        late = [n for n in s.db["notifications"] if n["kind"] == "late"]
        assert len(late) == 1 and late[0]["audience"] == {"role": "coordinator"}


class TestIncidentHelpers:
    @covers("REQ-SAFE-02")
    def test_find_skips_non_matching_incidents(self):
        s = make()
        incidents.create_incident(s, CTX, "r1", "Low", "First incident.")
        with pytest.raises(DomainError) as e:
            incidents.assign_incident(s, CTX, "i999", SEED_EMAIL)
        assert e.value.status == 404

    @covers("REQ-SAFE-02")
    def test_create_incident_validation(self):
        s = make()
        for args, status in (
            (("r1", "Extreme", "x"), 400),
            (("r1", "Low", "   "), 400),
            (("r1", "Low", "x", "v999"), 404),
            (("r2", "Low", "x", "v1"), 400),  # v1 belongs to r1, not r2
        ):
            with pytest.raises(DomainError) as e:
                incidents.create_incident(s, CTX, *args)
            assert e.value.status == status, args

    @covers("REQ-SAFE-02")
    def test_assign_resolved_and_empty_owner_and_empty_resolution(self):
        s = make()
        visit = service.log_visit(s, CTX, "r1", "Needs attention", "Missed medication.")
        inc = incidents.open_for_visit(s, CTX, visit)
        with pytest.raises(DomainError) as e:
            incidents.assign_incident(s, CTX, inc["id"], "   ")
        assert e.value.status == 400
        with pytest.raises(DomainError) as e:
            incidents.resolve_incident(s, CTX, inc["id"], "  ")
        assert e.value.status == 400
        incidents.resolve_incident(s, CTX, inc["id"], "Called the family.")
        with pytest.raises(DomainError) as e:
            incidents.assign_incident(s, CTX, inc["id"], SEED_EMAIL)
        assert e.value.status == 409

    @covers("REQ-SAFE-02")
    def test_reassigning_keeps_an_in_progress_incident_in_progress(self):
        s = make()
        visit = service.log_visit(s, CTX, "r1", "Needs attention", "Missed medication.")
        inc = incidents.open_for_visit(s, CTX, visit)
        assert incidents.assign_incident(s, CTX, inc["id"], SEED_EMAIL)["status"] == "In progress"
        again = incidents.assign_incident(s, CTX, inc["id"], SEED_EMAIL)
        assert again["status"] == "In progress" and again["owner"] == SEED_EMAIL

    @covers("REQ-SAFE-02")
    def test_create_incident_with_a_matching_visit_succeeds(self):
        s = make()
        inc = incidents.create_incident(s, CTX, "r1", "Low", "Parent reported a missed call.", "v1")
        assert inc["visitId"] == "v1" and inc["requestId"] == "r1"


class TestPrivacyHelpers:
    @covers("REQ-PRIV-01")
    def test_list_consents_is_role_scoped(self):
        s = make()
        assert len(privacy.list_consents(s)) == 9  # seed: 6 caregivers + 3 parents
        assert len(privacy.list_consents(s, {"role": "coordinator"})) == 9
        cg = privacy.list_consents(s, {"email": "c@example.com", "role": "caregiver", "caregiverId": "c1"})
        assert len(cg) == 1 and cg[0]["personId"] == "c1"
        fam = privacy.list_consents(s, {"email": "f@example.com", "role": "family", "requestId": "r1"})
        assert len(fam) == 1 and fam[0]["personId"] == "r1"
        assert privacy.list_consents(s, {"role": "admin"}) == []

    @covers("REQ-PRIV-01")
    def test_withdraw_and_erase_unknown_ids_are_404(self):
        s = make()
        with pytest.raises(DomainError) as e:
            privacy.withdraw_consent(s, CTX, "con999")
        assert e.value.status == 404
        with pytest.raises(DomainError) as e:
            privacy.erase_caregiver(s, CTX, "nope")
        assert e.value.status == 404


class TestNotificationHelpers:
    @covers("REQ-NOTIF-01")
    def test_append_rejects_unknown_kind_and_accepts_a_string_timestamp(self):
        s = make()
        with pytest.raises(DomainError) as e:
            notifications.append_notification(s, {"role": "coordinator"}, "bogus", "x")
        assert e.value.status == 400
        n = notifications.append_notification(s, {"role": "coordinator"}, "late", "x", created_at="2026-11-16T10:00:00+00:00")
        assert n["createdAt"] == "2026-11-16T10:00:00+00:00"

    @covers("REQ-NOTIF-01")
    def test_visible_notifications_by_role(self):
        s = make()
        notifications.append_notification(s, {"role": "caregiver", "caregiverId": "c6"}, "late", "For c6.")
        notifications.append_notification(s, {"role": "coordinator"}, "late", "For the coordinator.")
        cg = {"email": "c@example.com", "role": "caregiver", "caregiverId": "c6"}
        mine = notifications.visible_notifications(s, cg)
        assert len(mine) == 1 and mine[0]["audience"]["role"] == "caregiver"
        other = notifications.visible_notifications(s, {"email": "x@example.com", "role": "caregiver", "caregiverId": "c1"})
        assert other == []
        assert notifications.visible_notifications(s, {"role": "admin"}) == []
        assert len(notifications.visible_notifications(s)) == 2

    @covers("REQ-NOTIF-01")
    def test_notify_late_arrival_dedupes_while_unread(self):
        s = make()
        v = service.log_visit(s, CTX, "r1", "Good", "Tea.", planned_at="2026-11-16T10:00:00+00:00")
        created = notifications.notify_late_arrival(s, CTX, v, 45)
        assert len(created) == 2
        assert {n["audience"]["role"] for n in created} == {"coordinator", "family"}
        assert notifications.notify_late_arrival(s, CTX, v, 45) == []
        # Once both are read, a re-check notifies again.
        for n in created:
            notifications.mark_read(s, CTX, n["id"], {"email": SEED_EMAIL, "role": "coordinator"})
        assert len(notifications.notify_late_arrival(s, CTX, v, 45)) == 2

    @covers("REQ-NOTIF-01")
    def test_notify_for_visit_without_a_matching_request(self):
        s = make()
        ghost = {"id": "v901", "requestId": "ghost", "caregiverId": "c6", "date": "2026-11-16", "mood": "Needs attention", "note": "Orphan."}
        created = notifications.notify_for_visit(s, CTX, ghost)
        assert len(created) == 1 and created[0]["audience"] == {"role": "coordinator"}

    @covers("REQ-NOTIF-01")
    def test_late_arrival_without_a_matching_request_notifies_only_the_coordinator(self):
        s = make()
        ghost = {"id": "v902", "requestId": "ghost", "caregiverId": "c6", "date": "2026-11-16",
                 "mood": "Good", "note": "Orphan.", "plannedAt": "2026-11-16T10:00:00+00:00"}
        created = notifications.notify_late_arrival(s, CTX, ghost, 45)
        assert len(created) == 1 and created[0]["audience"] == {"role": "coordinator"}

    @covers("REQ-NOTIF-01")
    def test_missed_check_skips_unmatched_unvisited_and_recent(self):
        s = make()
        # A matched request with no visit yet has no baseline and is skipped.
        drive_to_matched(s)
        created = notifications.check_missed_visits(s, CTX, today=TODAY)
        assert len(created) == 2  # only the seed request r1 is overdue
        assert [n["audience"].get("requestId") for n in created if n["audience"]["role"] == "family"] == ["r1"]
        # One day after the seed visit, nothing is overdue.
        assert notifications.check_missed_visits(s, CTX, today=date(2026, 11, 13)) == []

    @covers("REQ-NOTIF-01")
    def test_mark_read_unknown_and_already_read(self):
        s = make()
        n = notifications.append_notification(s, {"role": "coordinator"}, "late", "x")
        coord = {"email": SEED_EMAIL, "role": "coordinator"}
        with pytest.raises(DomainError) as e:
            notifications.mark_read(s, CTX, "n999", coord)
        assert e.value.status == 404
        read = notifications.mark_read(s, CTX, n["id"], coord)
        assert read["readAt"] is not None
        again = notifications.mark_read(s, CTX, n["id"], coord)
        assert again["readAt"] == read["readAt"]


class TestMatchingHelpers:
    @covers("REQ-MATCH-02")
    def test_unknown_ids_are_404(self):
        s = make()
        with pytest.raises(DomainError) as e:
            matching.set_profile(s, CTX, "nope", languages=["Hindi"])
        assert e.value.status == 404
        with pytest.raises(DomainError) as e:
            matching.suggest_caregivers(s, "nope")
        assert e.value.status == 404

    @covers("REQ-MATCH-02")
    def test_set_profile_validation(self):
        s = make()
        cases = [
            ({"languages": []}, 400),
            ({"languages": "Hindi"}, 400),
            ({"languages": ["  "]}, 400),
            ({"availability": "not-a-dict"}, 400),
            ({"availability": {"days": ["Funday"], "hours": "9-5"}}, 400),
            ({"availability": {"days": [], "hours": "9-5"}}, 400),
            ({"availability": {"days": ["Monday"], "hours": "  "}}, 400),
            ({"capacity_visits_per_week": 22}, 400),
            ({"capacity_visits_per_week": True}, 400),
            ({"capacity_visits_per_week": -1}, 400),
        ]
        for kwargs, status in cases:
            with pytest.raises(DomainError) as e:
                matching.set_profile(s, CTX, "c1", **kwargs)
            assert e.value.status == status, kwargs

    @covers("REQ-MATCH-02")
    def test_set_profile_updates_only_the_fields_given(self):
        s = make()
        c = service.add_caregiver(s, CTX, "Noor", "Hyderabad", date(2005, 1, 1))
        out = matching.set_profile(s, CTX, c["id"], languages=["Hindi"])
        assert out["languages"] == ["Hindi"] and out["availability"] == {"days": [], "hours": ""} and out["capacityVisitsPerWeek"] is None
        out = matching.set_profile(s, CTX, c["id"], availability={"days": ["Monday"], "hours": "09:00-12:00"})
        assert out["languages"] == ["Hindi"] and out["availability"]["days"] == ["Monday"]
        out = matching.set_profile(s, CTX, c["id"], capacity_visits_per_week=4)
        assert out["capacityVisitsPerWeek"] == 4 and out["languages"] == ["Hindi"]

    @covers("REQ-MATCH-02")
    def test_suggestions_without_a_language_preference_and_without_capacity(self):
        s = make()
        # A certified, verified caregiver with no matching profile at all.
        c = service.add_caregiver(s, CTX, "Noor", "Hyderabad", date(2005, 1, 1))
        service.start_training(s, CTX, c["id"])
        for i in range(5):
            service.toggle_module(s, CTX, c["id"], i)
        service.certify(s, CTX, c["id"])
        for item in ("identity", "background", "references"):
            service.set_verification(s, CTX, c["id"], item, "Verified")
        r = service.create_request(s, CTX, "Test family", "Mr. Test", "Hyderabad", 2, "Companionship")  # no language
        mine = next(x for x in matching.suggest_caregivers(s, r["id"])["suggestions"] if x["caregiverId"] == c["id"])
        assert mine["breakdown"]["language"]["note"] == "No language preference on this request."
        assert mine["breakdown"]["language"]["points"] == 25
        assert mine["breakdown"]["capacity"]["note"] == "Capacity not set."
        assert mine["breakdown"]["capacity"]["points"] == 0


class TestReportsHelpers:
    @covers("REQ-REP-01")
    def test_summary_tolerates_missing_and_bad_matched_dates(self):
        s = make()
        s.db["requests"].append({"id": "r9", "family": "X", "parent": "Mr. X", "city": "Pune", "visitsPerWeek": 2, "need": "Companionship", "status": "Matched", "caregiverId": "c6", "matchedAt": None})
        out = reports.summary(s, today=TODAY)
        assert out["placement"] == {"matchedRequests": 2, "totalRequests": 4, "rate": 0.5}
        assert out["visits"]["expected"] == 0  # seed r1 matched 2026-11-10: 0 full weeks by 11-16
        s.db["requests"][-1]["matchedAt"] = "not-a-date"
        out = reports.summary(s, today=TODAY)
        assert out["placement"]["matchedRequests"] == 2
        assert out["visits"]["expected"] == 0

    @covers("REQ-REP-01")
    def test_retention_counts_a_caregiver_from_the_audit_history(self):
        s = make()
        # Match r2 to c5 (writes a request.matched audit event), then undo the
        # match in the stored state: the caregiver still counts as ever-matched.
        service.match(s, CTX, "r2", "c5")
        c5 = next(c for c in s.db["caregivers"] if c["id"] == "c5")
        c5["stage"] = "Certified"
        r2 = next(r for r in s.db["requests"] if r["id"] == "r2")
        r2["status"] = "Open"
        r2["caregiverId"] = None
        # Malformed audit events must not break the summary either.
        s.db["audit"].append({"action": "request.matched", "after": "junk"})
        s.db["audit"].append({"action": "request.matched", "after": {"caregiver": None}})
        out = reports.summary(s, today=TODAY)
        assert out["retention"] == {"everMatched": 2, "currentlyMatched": 1, "rate": 0.5}


class TestServiceHelpers:
    @covers("REQ-VIS-01", "REQ-EVV-01")
    def test_planned_time_accepts_a_datetime_and_rejects_garbage(self):
        s = make()
        v = service.log_visit(s, CTX, "r1", "Good", "Tea.", planned_at=datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc))
        assert v["plannedAt"] == "2026-11-16T10:00:00+00:00"
        with pytest.raises(DomainError) as e:
            service.log_visit(s, CTX, "r1", "Good", "Tea.", planned_at="not-a-time")
        assert e.value.status == 400


class TestStoreHelpers:
    @covers("REQ-STORE-01", "REQ-TRC-06")
    def test_empty_sqlite_file_is_seeded(self, tmp_path):
        p = tmp_path / "saathi.sqlite"
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE other (x TEXT)")
        conn.commit()
        conn.close()
        s = Store(p)
        assert len(s.db["caregivers"]) == 6

    @covers("REQ-STORE-01", "REQ-TRC-06")
    def test_sqlite_with_a_wrong_state_schema_is_not_overwritten(self, tmp_path):
        p = tmp_path / "saathi.sqlite"
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE state (key TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError):
            Store(p)

    @covers("REQ-STORE-01", "REQ-TRC-06")
    def test_corrupt_value_in_the_state_table_is_not_overwritten(self, tmp_path):
        p = tmp_path / "saathi.sqlite"
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO state VALUES ('caregivers', 'not json{{')")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError):
            Store(p)

    @covers("REQ-STORE-01", "REQ-TRC-06")
    def test_non_object_legacy_json_is_not_overwritten(self, tmp_path):
        legacy = tmp_path / "db.json"
        legacy.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(RuntimeError):
            Store(tmp_path / "saathi.sqlite")
        assert legacy.read_text(encoding="utf-8") == "[1, 2, 3]"


class TestObservabilityHelpers:
    @covers("REQ-OBS-02")
    def test_json_formatter_includes_exception_info(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(observability.JsonFormatter())
        logger = logging.getLogger("saathi")
        logger.addHandler(handler)
        try:
            try:
                raise ValueError("boom")
            except ValueError:
                logger.exception("something failed")
        finally:
            logger.removeHandler(handler)
        lines = [json.loads(line) for line in stream.getvalue().strip().splitlines()]
        assert lines[-1]["msg"] == "something failed"
        assert "ValueError: boom" in lines[-1]["exc"]

    @covers("REQ-OBS-03")
    def test_metrics_render_covers_a_second_histogram(self):
        # Use a fresh Metrics instance: observing the global singleton here
        # would change the counts other tests assert on.
        m = observability.Metrics()
        m.observe("saathi_http_request_seconds", 0.1, route="/x")
        m.observe("other_histogram_seconds", 0.1, route="/x")
        out = m.render()
        assert "other_histogram_seconds" in out
        assert "saathi_http_request_seconds" in out

    @covers("REQ-OBS-05")
    def test_otel_helpers_without_the_otel_package(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "opentelemetry", None)
        assert observability.otel_trace_id() is None
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
        assert observability.setup_tracing(None) is False


class TestMainRoutes:
    @covers("REQ-OBS-04")
    def test_auth_me_returns_the_logged_in_user(self, client):
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json() == {"email": SEED_EMAIL, "role": "coordinator", "caregiverId": None, "requestId": None}

    @covers("REQ-OBS-02", "REQ-OBS-03")
    def test_unhandled_error_becomes_a_500(self, client, monkeypatch):
        from app import service as service_mod

        def boom(*args, **kwargs):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(service_mod, "get_state", boom)
        r = client.get("/api/state")
        assert r.status_code == 500 and r.json() == {"error": "Server error."}

    @covers("REQ-SAFE-02", "REQ-NOTIF-01")
    def test_escalation_failure_never_fails_the_visit(self, client, monkeypatch):
        from app import incidents as incidents_mod

        def boom(*args, **kwargs):
            raise DomainError(409, "already open")

        monkeypatch.setattr(incidents_mod, "open_for_visit", boom)
        r = client.post("/api/visits", json={"requestId": "r1", "mood": "Needs attention", "note": "Missed medication."})
        assert r.status_code == 201
        assert client.get("/api/incidents").json() == []
        notes = client.get("/api/notifications").json()
        assert len([n for n in notes if n["kind"] == "needs_attention"]) == 2

    @covers("REQ-SAFE-02", "REQ-NOTIF-01")
    def test_unexpected_escalation_error_never_fails_the_visit(self, client, monkeypatch):
        from app import incidents as incidents_mod

        def boom(*args, **kwargs):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(incidents_mod, "open_for_visit", boom)
        r = client.post("/api/visits", json={"requestId": "r1", "mood": "Needs attention", "note": "Missed medication."})
        assert r.status_code == 201
        assert client.get("/api/incidents").json() == []

    @covers("REQ-AFT-03")
    def test_documents_and_housing_routes(self, client):
        r = client.post("/api/caregivers/c1/documents", json={"type": "Aadhaar", "reference": "XXXX-XXXX-9999"})
        assert r.status_code == 201 and r.json()["documents"][-1]["reference"] == "XXXX-XXXX-9999"
        r = client.post("/api/caregivers/c1/housing", json={"status": "Stable", "details": "Rented room."})
        assert r.status_code == 200 and r.json()["housing"]["status"] == "Stable"
        client.post("/api/auth/users", json={"email": "fam@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"})
        as_user(client, "fam@example.com", "family-pass-1")
        assert client.post("/api/caregivers/c1/documents", json={"type": "Aadhaar", "reference": "X"}).status_code == 403
        assert client.post("/api/caregivers/c1/housing", json={"status": "Stable"}).status_code == 403

    @covers("REQ-MATCH-02")
    def test_profile_route(self, client):
        r = client.post("/api/caregivers/c1/profile", json={"languages": ["Hindi"], "availability": {"days": ["Monday"], "hours": "09:00-12:00"}, "capacityVisitsPerWeek": 3})
        assert r.status_code == 200 and r.json()["languages"] == ["Hindi"]
        client.post("/api/auth/users", json={"email": "fam@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"})
        as_user(client, "fam@example.com", "family-pass-1")
        assert client.post("/api/caregivers/c1/profile", json={"languages": ["Hindi"]}).status_code == 403

    @covers("REQ-EVV-01")
    def test_checkin_unknown_visit_and_other_caregivers_visit(self, client):
        assert client.post("/api/visits/v999/checkin", json={}).status_code == 404
        # Drive a second caregiver to a match, then try to check in as c6.
        cid = client.post("/api/caregivers", json={"name": "Noor", "city": "Hyderabad", "dateOfBirth": "2007-01-01"}).json()["id"]
        client.post(f"/api/caregivers/{cid}/start")
        for i in range(5):
            client.post(f"/api/caregivers/{cid}/modules/{i}")
        client.post(f"/api/caregivers/{cid}/certify")
        for item in ("identity", "background", "references"):
            client.post(f"/api/caregivers/{cid}/verification/{item}", json={"status": "Verified"})
        rid = client.post("/api/requests", json={"family": "Test", "parent": "Mr. Test", "city": "Hyderabad", "visitsPerWeek": 2, "need": "Companionship"}).json()["id"]
        client.post(f"/api/requests/{rid}/match", json={"caregiverId": cid})
        vid = client.post("/api/visits", json={"requestId": rid, "mood": "Good", "note": "Tea."}).json()["id"]
        client.post("/api/auth/users", json={"email": "imran@example.com", "role": "caregiver", "password": "care-pass-1", "caregiverId": "c6"})
        as_user(client, "imran@example.com", "care-pass-1")
        assert client.post(f"/api/visits/{vid}/checkin", json={}).status_code == 403

    @covers("REQ-PLAN-01")
    def test_care_plan_scope_and_missing_plan(self, client):
        assert client.get("/api/requests/nope/care-plan").status_code == 404
        assert client.get("/api/requests/r2/care-plan").status_code == 404  # no plan yet
        client.post("/api/auth/users", json={"email": "menon@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"})
        as_user(client, "menon@example.com", "family-pass-1")
        r = client.get("/api/requests/r1/care-plan")
        assert r.status_code == 200 and r.json()["goals"].startswith("Daily companionship")
        assert client.get("/api/requests/r2/care-plan").status_code == 403
        # The matched caregiver may toggle a task; a family may not.
        as_user(client, SEED_EMAIL, SEED_PASSWORD)
        client.post("/api/auth/users", json={"email": "imran@example.com", "role": "caregiver", "password": "care-pass-1", "caregiverId": "c6"})
        as_user(client, "imran@example.com", "care-pass-1")
        assert client.post("/api/requests/r1/care-plan/tasks/t2/toggle").status_code == 200
        as_user(client, "menon@example.com", "family-pass-1")
        assert client.post("/api/requests/r1/care-plan/tasks/t2/toggle").status_code == 403

    @covers("REQ-NOTIF-01")
    def test_check_missed_route(self, client):
        r = client.post("/api/notifications/check-missed")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @covers("REQ-PRIV-01")
    def test_consents_route(self, client):
        r = client.get("/api/consents")
        assert r.status_code == 200 and len(r.json()) == 9
        client.post("/api/auth/users", json={"email": "imran@example.com", "role": "caregiver", "password": "care-pass-1", "caregiverId": "c6"})
        as_user(client, "imran@example.com", "care-pass-1")
        mine = client.get("/api/consents").json()
        assert len(mine) == 1 and mine[0]["personId"] == "c6"

    @covers("REQ-FAM-01", "REQ-RBAC-01")
    def test_family_request_without_a_linked_user_record_still_succeeds(self, tmp_path, monkeypatch):
        from app import auth as auth_mod

        monkeypatch.setenv("SAATHI_SEED_PASSWORD", SEED_PASSWORD)
        app = create_app(tmp_path / "db.json", public_dir=None)
        c = TestClient(app)
        c.headers["Authorization"] = f"Bearer {login(c)}"
        c.post("/api/auth/users", json={"email": "fam@example.com", "role": "family", "password": "family-pass-1", "requestId": "r1"})
        as_user(c, "fam@example.com", "family-pass-1")
        # The linked-user lookup is defensive: in production the authenticated
        # user always has a record. Force the lookup to fail on the route's
        # call (the 2nd find_user after patching: the 1st is require_user's).
        calls = {"n": 0}
        real_find = auth_mod.find_user

        def flaky(db, email):
            calls["n"] += 1
            if calls["n"] >= 2:
                return None
            return real_find(db, email)

        monkeypatch.setattr(auth_mod, "find_user", flaky)
        r = c.post("/api/requests", json={"family": "Menon", "parent": "Mr. Raghavan", "city": "Pune", "visitsPerWeek": 2, "need": "Companionship"})
        assert r.status_code == 201, r.text
        # The link step was skipped, so the family's record still points at r1.
        assert next(u for u in app.state.store.db["users"] if u["email"] == "fam@example.com")["requestId"] == "r1"


class TestModuleEntryPoint:
    @covers("REQ-OBS-04")
    def test_main_module_imports_cleanly(self):
        # Plain import: the __main__ guard is False, so the server is not started.
        import app.__main__ as entry

        assert entry.__file__.endswith("__main__.py")
        assert hasattr(entry, "uvicorn")

    @covers("REQ-OBS-04")
    def test_main_module_entry_point_calls_uvicorn(self, monkeypatch):
        import runpy

        import uvicorn

        calls = []
        monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
        monkeypatch.setenv("PORT", "8123")
        # The import test above put app.__main__ in sys.modules; drop it so
        # runpy loads a fresh copy instead of warning about the stale one.
        monkeypatch.delitem(sys.modules, "app.__main__", raising=False)
        runpy.run_module("app", run_name="__main__")
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args == ("app.main:app",)
        assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == 8123 and kwargs["access_log"] is False

    @covers("REQ-OBS-04")
    def test_python_m_app_boots_and_serves_health(self):
        import socket

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        env = dict(os.environ)
        env["PORT"] = str(port)
        proc = subprocess.Popen([sys.executable, "-m", "app"], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.time() + 20
            while True:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as resp:
                        assert resp.status == 200
                        break
                except Exception:
                    if time.time() > deadline:
                        raise AssertionError("server did not start in 20 seconds")
                    time.sleep(0.2)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
