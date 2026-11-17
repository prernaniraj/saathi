"""Structured logs, request correlation and metrics. Standard library only."""
import contextvars
import json
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone

from .trace import implements

request_id_var = contextvars.ContextVar("request_id", default="-")
trace_id_var = contextvars.ContextVar("trace_id", default="-")
log = logging.getLogger("saathi")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
            "trace_id": trace_id_var.get(),
        }
        out.update(getattr(record, "fields", {}))
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("saathi")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False


def event(level: int, msg: str, **fields) -> None:
    log.log(level, msg, extra={"fields": fields})


BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)


class Metrics:
    """Tiny Prometheus-format registry: labelled counters and one histogram type."""

    def __init__(self):
        self._lock = threading.Lock()
        self.counters: dict = defaultdict(float)
        self.hist: dict = {}

    def inc(self, name: str, **labels) -> None:
        with self._lock:
            self.counters[(name, tuple(sorted(labels.items())))] += 1

    def observe(self, name: str, value: float, **labels) -> None:
        with self._lock:
            h = self.hist.setdefault((name, tuple(sorted(labels.items()))), {"b": [0] * len(BUCKETS), "n": 0, "sum": 0.0})
            for i, edge in enumerate(BUCKETS):
                if value <= edge:
                    h["b"][i] += 1
            h["n"] += 1
            h["sum"] += value

    def value(self, name: str, **labels) -> float:
        return self.counters.get((name, tuple(sorted(labels.items()))), 0.0)

    @staticmethod
    def _fmt(labels: tuple, extra: str = "") -> str:
        parts = [f'{k}="{v}"' for k, v in labels] + ([extra] if extra else [])
        return "{" + ",".join(parts) + "}" if parts else ""

    def render(self) -> str:
        lines = []
        with self._lock:
            for name in sorted({k[0] for k in self.counters}):
                lines.append(f"# TYPE {name} counter")
                lines += [f"{n}{self._fmt(l)} {v:g}" for (n, l), v in sorted(self.counters.items()) if n == name]
            for name in sorted({k[0] for k in self.hist}):
                lines.append(f"# TYPE {name} histogram")
                for (n, l), h in sorted(self.hist.items()):
                    if n != name:
                        continue
                    lines += [f'{n}_bucket{self._fmt(l, f"le=\"{e}\"")} {c}' for e, c in zip(BUCKETS, h["b"])]
                    lines.append(f'{n}_bucket{self._fmt(l, "le=\"+Inf\"")} {h["n"]}')
                    lines.append(f"{n}_sum{self._fmt(l)} {h['sum']:.6f}")
                    lines.append(f"{n}_count{self._fmt(l)} {h['n']}")
        return "\n".join(lines) + "\n"


metrics = Metrics()


def otel_trace_id() -> str | None:
    """Trace id from an active OpenTelemetry span, if the optional packages are installed and tracing is on."""
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else None
    except ImportError:
        return None


@implements("REQ-OBS-05")
def setup_tracing(app) -> bool:
    """Optional: export traces when OTEL_EXPORTER_OTLP_ENDPOINT is set and the `otel` extra is installed."""
    import os

    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        event(logging.WARNING, "OTEL_EXPORTER_OTLP_ENDPOINT is set but the otel extra is not installed")
        return False
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    return True
