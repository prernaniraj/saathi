"""Audit hash anchoring (REQ-TRC-07): the latest audit hash lives outside the database.

The hash chain proves history was not edited, but someone with file access
could rewrite the whole chain and the database together. The anchor file
holds the latest sequence and hash next to the database, written after every
save, so a rewritten database alone no longer matches. /readyz returns 503
when the anchor does not match the chain head.

Honest limit: the anchor file sits on the same disk as the database, so it
catches tampering that rewrites the database alone, not someone with full
access to all the files. Truly external anchoring needs a separate store.

An anchor write failure never fails the change that caused it: the database
is already committed by then, so the failure is logged and counted, and
/readyz reports the mismatch instead.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .audit import GENESIS
from .observability import event, metrics
from .trace import implements

ANCHOR_FILENAME = "audit-anchor.json"


def anchor_path_for(store_path) -> Path | None:
    """The anchor file next to a database file, or None for an in-memory store."""
    if not store_path:
        return None
    return Path(store_path).parent / ANCHOR_FILENAME


def head_of(events: list) -> dict:
    """The chain head as an anchor payload: sequence and hash, or genesis when empty."""
    if not events:
        return {"seq": 0, "hash": GENESIS}
    head = events[-1]
    return {"seq": head["seq"], "hash": head["hash"]}


def _anchor_payload(events: list) -> dict:
    payload = head_of(events)
    payload["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return payload


def _write_anchor_file(path: Path, payload: dict) -> None:
    """Write the anchor atomically: temp file in the same directory, then replace."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@implements("REQ-TRC-07")
def record_anchor(store) -> None:
    """Record the current chain head as the anchor, after a save has committed.

    For a durable store this writes the anchor file; for an in-memory store it
    keeps the anchor on the store object. A write failure is logged and counted
    and never raised, so it cannot fail an already-committed change.
    """
    payload = _anchor_payload(store.db.get("audit") or [])
    path = anchor_path_for(getattr(store, "path", None))
    if path is None:
        store._anchor = payload
        metrics.inc("saathi_anchor_writes_total", status="ok")
        return
    try:
        _write_anchor_file(path, payload)
    except OSError as e:
        metrics.inc("saathi_anchor_writes_total", status="failed")
        event(logging.ERROR, "audit anchor write failed", anchor_path=str(path), reason=str(e), spec_refs=["REQ-TRC-07"])
        return
    store._anchor = payload
    metrics.inc("saathi_anchor_writes_total", status="ok")


def _read_anchor_file(path: Path) -> dict | None:
    """The stored anchor, or None when there is no file. Raises ValueError on corrupt content."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("seq"), int) or not isinstance(data.get("hash"), str):
        raise ValueError("anchor file does not hold a sequence and hash")
    return data


@implements("REQ-TRC-07")
def verify_anchor(store) -> dict:
    """Compare the stored anchor with the chain head.

    Returns {"ok": bool, "events": int, "head": {...}, "anchor": {...} | None,
    "reason": str | None}. An empty chain with no anchor yet is ok: there is
    nothing to anchor. A chain with events but no anchor, a corrupt anchor
    file, or a head that differs from the anchor is not ok.
    """
    events = store.db.get("audit") or []
    head = head_of(events)
    path = anchor_path_for(getattr(store, "path", None))
    anchor = None
    try:
        if path is not None:
            anchor = _read_anchor_file(path)
        else:
            anchor = getattr(store, "_anchor", None)
    except (ValueError, OSError) as e:
        return {"ok": False, "events": len(events), "head": head, "anchor": None, "reason": f"anchor unreadable: {e}"}
    if anchor is None:
        if not events:
            return {"ok": True, "events": 0, "head": head, "anchor": None, "reason": None}
        return {"ok": False, "events": len(events), "head": head, "anchor": None, "reason": "anchor missing"}
    if anchor.get("seq") != head["seq"] or anchor.get("hash") != head["hash"]:
        return {"ok": False, "events": len(events), "head": head, "anchor": {"seq": anchor.get("seq"), "hash": anchor.get("hash")}, "reason": "anchor does not match chain head"}
    return {"ok": True, "events": len(events), "head": head, "anchor": {"seq": anchor.get("seq"), "hash": anchor.get("hash")}, "reason": None}
