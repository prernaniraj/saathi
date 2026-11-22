"""Append-only, hash-chained audit log. Each event commits to the one before it."""
import hashlib
import json
from datetime import datetime, timezone

from .domain import Change, Ctx
from .erasure import decrypt_event
from .trace import implements

GENESIS = "0" * 64


def _digest(event: dict) -> str:
    body = {k: v for k, v in event.items() if k != "hash"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, events: list, keys: dict | None = None):
        self.events = events
        # Per-person audit keys (REQ-PRIV-03). Reads decrypt through them;
        # a key deleted by erasure makes that person's fields unreadable.
        self._keys = keys if keys is not None else {}

    @implements("REQ-TRC-01", "REQ-TRC-02")
    def append(self, ctx: Ctx, action: str, change: Change, spec_refs: tuple) -> dict:
        event = {
            "seq": len(self.events) + 1,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "request_id": ctx.request_id,
            "trace_id": ctx.trace_id,
            "actor": ctx.actor,
            "action": action,
            "entity_type": change.entity_type,
            "entity_id": change.entity_id,
            "before": change.before,
            "after": change.after,
            "spec_refs": list(spec_refs),
            "prev_hash": self.events[-1]["hash"] if self.events else GENESIS,
        }
        event["hash"] = _digest(event)
        self.events.append(event)
        return event

    @implements("REQ-TRC-02")
    def verify(self) -> dict:
        prev = GENESIS
        for i, ev in enumerate(self.events, 1):
            if ev.get("seq") != i or ev.get("prev_hash") != prev or ev.get("hash") != _digest(ev):
                return {"ok": False, "events": len(self.events), "broken_at": i}
            prev = ev["hash"]
        return {"ok": True, "events": len(self.events), "broken_at": None}

    @implements("REQ-TRC-03")
    def query(self, entity_id: str | None = None, action: str | None = None, limit: int = 50) -> list:
        rows = [e for e in reversed(self.events) if (not entity_id or e["entity_id"] == entity_id) and (not action or e["action"] == action)]
        # Reads return decrypted copies; the stored events are never modified.
        # Fields whose key was deleted by erasure come back as "[erased]".
        return [decrypt_event(self._keys, e) for e in rows[:limit]]
