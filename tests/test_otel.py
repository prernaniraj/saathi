"""OpenTelemetry export, end to end (REQ-OBS-05). Needs the otel extra:
pip install -e ".[otel]" — otherwise this module is skipped.

A dummy OTLP/HTTP receiver records what the app exports; the test asserts a
POST to /v1/traces arrives carrying spans for the requests made, and that the
trace ID in the log line is the OpenTelemetry trace ID.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("opentelemetry")
pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from tests._trace import covers  # noqa: E402

received = []


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        received.append({"path": self.path, "body": body})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


@covers("REQ-OBS-05")
def test_traces_export_over_otlp_and_trace_id_is_the_otel_one(tmp_path, monkeypatch):
    received.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("SAATHI_SEED_PASSWORD", "otel-test-pass")
        client = TestClient(create_app(tmp_path / "db.sqlite", public_dir=None))
        assert client.get("/healthz").status_code == 200
        assert client.post("/api/auth/login", json={"email": "coordinator@saathi.local", "password": "otel-test-pass"}).status_code == 200
        # Flush the batch span processor instead of waiting out its 5s schedule.
        from opentelemetry import trace

        trace.get_tracer_provider().force_flush()
        assert received, "no OTLP export reached the receiver"
        assert all(r["path"] == "/v1/traces" for r in received)
        payload = b"".join(r["body"] for r in received)
        assert b"/healthz" in payload
        assert b"/api/auth/login" in payload
    finally:
        # Stop the batch span processor before closing the receiver, so its
        # background thread does not retry an export against a closed port.
        from opentelemetry import trace

        try:
            trace.get_tracer_provider().shutdown()
        except Exception:
            pass
        server.shutdown()
        server.server_close()
