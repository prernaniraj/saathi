"""Audit anchor tests (REQ-TRC-07). Need dev extras: pip install -e '.[dev]' && pytest"""
import json
from datetime import date

from fastapi.testclient import TestClient

from app import anchor, service
from app.audit import AuditLog
from app.domain import Change, Ctx
from app.main import create_app
from app.observability import metrics
from app.store import Store
from tests._trace import covers

CTX = Ctx("req-1", "t" * 32, "coordinator")
DOB = date(2005, 5, 5)
SEED_PASSWORD = "test-coordinator-password"


def _rewritten_chain(events_count=1):
    """A valid hash chain built from scratch: chain verify passes, anchor must fail."""
    log = AuditLog([])
    for i in range(events_count):
        log.append(CTX, "caregiver.created", Change("caregiver", f"cx{i}", None, {}, {}), ("REQ-PIPE-01",))
    return log.events


@covers("REQ-TRC-07")
def test_empty_chain_with_no_anchor_is_ok():
    store = Store()
    result = anchor.verify_anchor(store)
    assert result["ok"] is True and result["events"] == 0


@covers("REQ-TRC-07")
def test_anchor_matches_after_a_change():
    store = Store()
    service.add_caregiver(store, CTX, "Divya", "Pune", DOB)
    result = anchor.verify_anchor(store)
    assert result["ok"] is True and result["events"] == 1
    assert result["head"] == result["anchor"]


@covers("REQ-TRC-07")
def test_rewritten_chain_is_caught_by_anchor_even_though_chain_verifies():
    store = Store()
    service.add_caregiver(store, CTX, "Divya", "Pune", DOB)
    assert store.audit.verify()["ok"] is True
    store.db["audit"] = _rewritten_chain(1)
    assert store.audit.verify()["ok"] is True
    result = anchor.verify_anchor(store)
    assert result["ok"] is False and result["reason"] == "anchor does not match chain head"


@covers("REQ-TRC-07")
def test_durable_store_writes_anchor_file(tmp_path):
    store = Store(tmp_path / "test.sqlite", seed_password=SEED_PASSWORD)
    service.add_caregiver(store, CTX, "Divya", "Pune", DOB)
    anchor_file = tmp_path / "audit-anchor.json"
    assert anchor_file.exists()
    data = json.loads(anchor_file.read_text(encoding="utf-8"))
    head = store.db["audit"][-1]
    assert (data["seq"], data["hash"]) == (head["seq"], head["hash"])
    assert anchor.verify_anchor(store)["ok"] is True


@covers("REQ-TRC-07")
def test_missing_and_corrupt_anchor_files_fail_verification(tmp_path):
    store = Store(tmp_path / "test.sqlite", seed_password=SEED_PASSWORD)
    service.add_caregiver(store, CTX, "Divya", "Pune", DOB)
    anchor_file = tmp_path / "audit-anchor.json"
    anchor_file.unlink()
    assert anchor.verify_anchor(store)["ok"] is False
    anchor_file.write_text("not json", encoding="utf-8")
    assert anchor.verify_anchor(store)["ok"] is False
    store.save()
    assert anchor.verify_anchor(store)["ok"] is True


@covers("REQ-TRC-07")
def test_anchor_write_failure_does_not_fail_the_change(tmp_path, monkeypatch):
    store = Store(tmp_path / "test.sqlite", seed_password=SEED_PASSWORD)
    before = metrics.value("saathi_anchor_writes_total", status="failed")

    def _boom(path, payload):
        raise OSError("disk full")

    monkeypatch.setattr(anchor, "_write_anchor_file", _boom)
    created = service.add_caregiver(store, CTX, "Divya", "Pune", DOB)
    assert created["name"] == "Divya"
    assert len(store.db["audit"]) == 1
    assert metrics.value("saathi_anchor_writes_total", status="failed") == before + 1
    assert anchor.verify_anchor(store)["ok"] is False


@covers("REQ-TRC-07")
def test_readyz_reports_anchor_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("SAATHI_SEED_PASSWORD", SEED_PASSWORD)
    app = create_app(tmp_path / "db.json", public_dir=None)
    client = TestClient(app)
    assert client.get("/readyz").status_code == 200
    app.state.store.db["audit"] = _rewritten_chain(1)
    assert app.state.store.audit.verify()["ok"] is True
    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "audit anchor mismatch"
    assert body["ok"] is False and body["anchor_ok"] is False
