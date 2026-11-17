"""Rule, audit and metrics tests. Standard library only: run with `python -m unittest tests.test_service`."""
import io
import json
import logging
import unittest

from app import service
from app.domain import Ctx, DomainError
from app.observability import JsonFormatter, metrics, request_id_var
from app.store import Store
from tests._trace import covers

CTX = Ctx("req-1", "t" * 32, "coordinator")


def make():
    return Store()


class PipelineRules(unittest.TestCase):
    @covers("REQ-PIPE-01")
    def test_add_caregiver_starts_in_applied(self):
        s = make()
        c = service.add_caregiver(s, CTX, "Divya", "Pune")
        self.assertEqual((c["stage"], c["modules"]), ("Applied", [0, 0, 0, 0, 0]))

    @covers("REQ-PIPE-02")
    def test_training_starts_only_from_applied(self):
        s = make()
        self.assertEqual(service.start_training(s, CTX, "c1")["stage"], "Training")
        with self.assertRaises(DomainError) as e:
            service.start_training(s, CTX, "c2")
        self.assertEqual(e.exception.status, 409)

    @covers("REQ-PIPE-03")
    def test_modules_change_only_during_training(self):
        s = make()
        self.assertEqual(service.toggle_module(s, CTX, "c2", 2)["modules"][2], 1)
        with self.assertRaises(DomainError):
            service.toggle_module(s, CTX, "c4", 0)
        with self.assertRaises(DomainError) as e:
            service.toggle_module(s, CTX, "c2", 9)
        self.assertEqual(e.exception.status, 400)

    @covers("REQ-PIPE-04")
    def test_certify_needs_all_five_modules(self):
        s = make()
        with self.assertRaises(DomainError):
            service.certify(s, CTX, "c2")
        for i in (2, 3, 4):
            service.toggle_module(s, CTX, "c2", i)
        self.assertEqual(service.certify(s, CTX, "c2")["stage"], "Certified")


class RequestsAndVisits(unittest.TestCase):
    @covers("REQ-FAM-01")
    def test_request_starts_open(self):
        r = service.create_request(make(), CTX, "Nair family", "Mrs. Nair", "Kochi", 2, "Companionship")
        self.assertEqual((r["status"], r["caregiverId"]), ("Open", None))

    @covers("REQ-MATCH-01")
    def test_match_needs_certified_caregiver_and_open_request(self):
        s = make()
        with self.assertRaises(DomainError):
            service.match(s, CTX, "r2", "c2")
        service.match(s, CTX, "r2", "c5")
        state = service.get_state(s)
        self.assertEqual(next(c for c in state["caregivers"] if c["id"] == "c5")["stage"], "Matched")
        with self.assertRaises(DomainError):
            service.match(s, CTX, "r2", "c4")

    @covers("REQ-VIS-01")
    def test_visit_rules(self):
        s = make()
        with self.assertRaises(DomainError):
            service.log_visit(s, CTX, "r2", "Good", "x")
        with self.assertRaises(DomainError):
            service.log_visit(s, CTX, "r1", "Great", "x")
        v = service.log_visit(s, CTX, "r1", "Needs attention", "Missed medication.")
        self.assertEqual(v["caregiverId"], "c6")


class Traceability(unittest.TestCase):
    @covers("REQ-TRC-01")
    def test_every_change_writes_an_audit_event_with_context_and_spec_refs(self):
        s = make()
        service.start_training(s, CTX, "c1")
        ev = s.db["audit"][-1]
        self.assertEqual((ev["action"], ev["actor"], ev["request_id"], ev["trace_id"]), ("caregiver.training_started", "coordinator", "req-1", "t" * 32))
        self.assertEqual((ev["entity_id"], ev["before"]["stage"], ev["after"]["stage"], ev["spec_refs"]), ("c1", "Applied", "Training", ["REQ-PIPE-02"]))

    @covers("REQ-TRC-02")
    def test_tampering_breaks_the_chain(self):
        s = make()
        service.start_training(s, CTX, "c1")
        service.add_caregiver(s, CTX, "Divya", "Pune")
        self.assertEqual(s.audit.verify(), {"ok": True, "events": 2, "broken_at": None})
        s.db["audit"][0]["actor"] = "someone-else"
        self.assertEqual(s.audit.verify()["broken_at"], 1)

    @covers("REQ-TRC-02")
    def test_removing_an_event_breaks_the_chain(self):
        s = make()
        for _ in range(3):
            service.add_caregiver(s, CTX, "X", "Pune")
        del s.db["audit"][1]
        self.assertFalse(s.audit.verify()["ok"])

    @covers("REQ-TRC-03")
    def test_audit_query_filters_newest_first(self):
        s = make()
        service.start_training(s, CTX, "c1")
        service.add_caregiver(s, CTX, "Divya", "Pune")
        self.assertEqual(s.audit.query()[0]["action"], "caregiver.created")
        self.assertEqual([e["entity_id"] for e in s.audit.query(entity_id="c1")], ["c1"])
        self.assertEqual(len(s.audit.query(action="nothing")), 0)

    @covers("REQ-TRC-05")
    def test_rejected_change_is_not_audited_but_is_counted(self):
        s = make()
        before = metrics.value("saathi_changes_rejected_total", action="caregiver.certified", status=409)
        with self.assertRaises(DomainError):
            service.certify(s, CTX, "c2")
        self.assertEqual(len(s.db["audit"]), 0)
        self.assertEqual(metrics.value("saathi_changes_rejected_total", action="caregiver.certified", status=409), before + 1)


class Observability(unittest.TestCase):
    @covers("REQ-OBS-02")
    def test_log_lines_are_json_and_carry_the_request_id(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("saathi")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        token = request_id_var.set("req-xyz")
        try:
            service.add_caregiver(make(), CTX, "Divya", "Pune")
        finally:
            request_id_var.reset(token)
            logger.removeHandler(handler)
        line = json.loads(stream.getvalue().strip().splitlines()[-1])
        self.assertEqual((line["msg"], line["request_id"], line["action"], line["spec_refs"]), ("change committed", "req-xyz", "caregiver.created", ["REQ-PIPE-01"]))

    @covers("REQ-OBS-03")
    def test_metrics_render_in_prometheus_format(self):
        service.add_caregiver(make(), CTX, "Divya", "Pune")
        metrics.observe("saathi_http_request_seconds", 0.02, route="/x")
        out = metrics.render()
        self.assertIn('saathi_changes_total{action="caregiver.created"}', out)
        self.assertIn('saathi_http_request_seconds_bucket{route="/x",le="+Inf"} 1', out)


class StoreSafety(unittest.TestCase):
    @covers("REQ-TRC-06")
    def test_corrupt_data_file_is_not_overwritten(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "db.json"
            p.write_text("{not json", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                Store(p)
            self.assertEqual(p.read_text(encoding="utf-8"), "{not json")

    @covers("REQ-TRC-06")
    def test_state_and_audit_survive_a_restart(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "db.json"
            s = Store(p)
            service.start_training(s, CTX, "c1")
            again = Store(p)
            self.assertEqual(again.audit.verify(), {"ok": True, "events": 1, "broken_at": None})


class TraceabilityTooling(unittest.TestCase):
    @covers("REQ-TRC-04")
    def test_every_implemented_requirement_has_code_and_a_test(self):
        from scripts import trace_matrix
        self.assertEqual(trace_matrix.main(), 0)


if __name__ == "__main__":
    unittest.main()
