# Saathi

Proof of concept for a care-leaver-to-caregiver pipeline: train young adults leaving orphanages as eldercare companions, certify them, match them to families abroad, and share visit updates. Python and FastAPI back end, plain HTML front end.

Read `SPEC.md` first. It holds the requirements, the researched feature backlog and the rule that every change needs a requirement, a test, an audit event, a log line and a metric.

## Run

```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m app                 # http://127.0.0.1:3000  (PORT=4000 to change)
pytest                        # all tests, needs the dev extras
python -m unittest tests.test_service   # rule, audit and metrics tests, no installs
python scripts/trace_matrix.py          # rebuilds docs/TRACEABILITY.md, exits 1 on gaps
```

Data is saved in `data/db.json`. Delete it to restore the sample data. A corrupt file stops startup so audit history is never overwritten.

## Pages

Dashboard `/`, Pipeline, Family requests, Visit log, Family view, and Audit (change history with a chain check).

## Observability and traceability

| What | Where |
| --- | --- |
| Request ID and trace ID on every response | `X-Request-ID`, `X-Trace-ID` headers; send `X-Request-ID` or `traceparent` to reuse yours |
| JSON logs with request ID, trace ID, action, actor, spec refs | stdout |
| Prometheus metrics | `GET /metrics` |
| Liveness and readiness | `GET /healthz`, `GET /readyz` (503 if the audit chain is broken) |
| Hash-chained audit log | `GET /api/audit`, `GET /api/audit/verify`, Audit page |
| Requirement to code to test | `docs/TRACEABILITY.md` |
| Optional OpenTelemetry export | `pip install -e ".[otel]"` and set `OTEL_EXPORTER_OTLP_ENDPOINT` |

Follow one change end to end: find its request ID in the logs, open the audit event with the same request ID, read its spec refs, and find the tests in the matrix.

## Layout

```
SPEC.md              requirements, API, research, backlog
app/domain.py        stages, modules, errors, context types
app/service.py       business rules; @mutation writes audit, log and metric
app/audit.py         hash-chained audit log
app/observability.py JSON logs, metrics, optional OpenTelemetry
app/store.py         JSON-file store (SQLite or Postgres is REQ-STORE-01)
app/main.py          FastAPI routes, middleware, static files
public/              front end
tests/               test_service.py (stdlib) and test_api.py (FastAPI client)
scripts/trace_matrix.py   traceability check
docs/TRACEABILITY.md      generated matrix
```
