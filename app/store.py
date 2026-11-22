"""SQLite store with an in-memory dict mirror. Standard library ``sqlite3`` only.

The dict in ``store.db`` is the working mirror (keys caregivers/requests/visits/
audit/seq/users/sessions/incidents/consents/breaches/notifications) and keeps the public surface existing code and tests
rely on. SQLite is the durable store: ``save()`` writes the whole state,
including audit events, in ONE transaction, so an audit append lands in the
same transaction as the change that caused it.

Layout: one table, ``state(key TEXT PRIMARY KEY, value TEXT)``, one row per
top-level key, each value JSON-encoded.

Legacy migration: on first boot, if the SQLite file does not exist but a legacy
``data/db.json`` sits next to it, the JSON is imported once and renamed to
``db.json.migrated`` (never deleted). A ``.json`` path holding valid JSON is
migrated the same way in place.

A corrupt file raises instead of re-seeding, so audit history is never silently
replaced.
"""
import json
import os
import sqlite3
import threading
from pathlib import Path

from .anchor import record_anchor
from .audit import AuditLog
from .auth import ensure_seed_coordinator
from .erasure import migrate_audit
from .seed import seed
from .trace import implements

STATE_TABLE = "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
ALL_KEYS = ("seq", "caregivers", "requests", "visits", "audit", "users", "sessions", "incidents", "consents", "breaches", "notifications", "personKeys", "auditCrypto")


class Store:
    """SQLite-backed store. Pass no path for an in-memory store (tests)."""

    @implements("REQ-TRC-06", "REQ-STORE-01")
    def __init__(self, path: str | Path | None = None, seed_password: str | None = None):
        self.path = Path(path) if path else None
        self.lock = threading.RLock()
        self._anchor = None  # Audit anchor for in-memory stores (REQ-TRC-07); durable stores write the anchor file.
        self.db = None
        if self.path:
            if self.path.exists():
                self.db = self._load_existing()
            else:
                legacy = self._legacy_json_path()
                if legacy is not None and legacy.exists():
                    self.db = self._import_legacy(legacy)
                else:
                    self.db = seed()
            self._normalize()
            self._maybe_migrate_audit()
            ensure_seed_coordinator(self.db, seed_password, print_password=True)
            self.save()
        else:
            self.db = seed()
            self._normalize()
            self._maybe_migrate_audit()
            ensure_seed_coordinator(self.db, seed_password, print_password=False)

    def _maybe_migrate_audit(self) -> None:
        """One-time migration for audit events stored in plaintext before
        REQ-PRIV-03 (see app/erasure.py). Runs only when the store has no
        ``auditCrypto`` marker, then sets the marker so it never runs again.
        A fresh store has no events, so the migration is a no-op there."""
        if self.db.get("auditCrypto"):
            return
        migrated = migrate_audit(self.db)
        self.db["auditCrypto"] = {"version": 1, "migratedEvents": migrated}

    def _normalize(self) -> None:
        self.db.setdefault("seq", 100)
        for key in ("caregivers", "requests", "visits", "audit", "users", "sessions", "incidents", "consents", "breaches", "notifications"):
            self.db.setdefault(key, [])
        self.db.setdefault("personKeys", {})
        self.db.setdefault("auditCrypto", None)

    def _legacy_json_path(self) -> Path | None:
        """The legacy JSON file next to a SQLite path, if the path is not itself that file."""
        if self.path.name == "db.json":
            return None
        return self.path.parent / "db.json"

    @staticmethod
    def _read_sqlite(path: Path) -> dict | None:
        """Read the state table. None when the file is an empty/new database.

        Raises sqlite3.DatabaseError when the file is not a readable database.
        """
        conn = sqlite3.connect(path)
        try:
            try:
                rows = conn.execute("SELECT key, value FROM state").fetchall()
            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    return None
                raise
            data = {}
            for key, value in rows:
                try:
                    data[key] = json.loads(value)
                except ValueError as e:
                    raise sqlite3.DatabaseError(f"corrupt value for {key!r}: {e}") from e
            return data
        finally:
            conn.close()

    @staticmethod
    def _import_legacy(json_path: Path) -> dict:
        """Read a legacy JSON store and rename it to ``<name>.migrated`` (never delete it)."""
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise RuntimeError(f"Cannot read {json_path}: {e}. Fix or move the file; it was not overwritten.") from e
        if not isinstance(data, dict):
            raise RuntimeError(f"Cannot read {json_path}: expected a JSON object. Fix or move the file; it was not overwritten.")
        os.replace(json_path, json_path.with_name(json_path.name + ".migrated"))
        return data

    def _load_existing(self) -> dict:
        try:
            data = self._read_sqlite(self.path)
        except sqlite3.DatabaseError:
            if self.path.suffix == ".json":
                # A .json path may still hold a legacy JSON store: import it.
                return self._import_legacy(self.path)
            raise RuntimeError(f"Cannot read {self.path}: the store is corrupt. Fix or move the file; it was not overwritten.")
        if data is None:
            return seed()
        return data

    @property
    def audit(self) -> AuditLog:
        return AuditLog(self.db["audit"], keys=self.db.setdefault("personKeys", {}))

    def next_id(self, prefix: str) -> str:
        self.db["seq"] += 1
        return f"{prefix}{self.db['seq'] - 1}"

    @implements("REQ-TRC-06", "REQ-STORE-01")
    def save(self) -> None:
        """Persist the whole state, audit included, in one SQLite transaction."""
        if not self.path:
            self.db.pop("_vacuum_needed", None)
            record_anchor(self)
            return
        vacuum = bool(self.db.pop("_vacuum_needed", False))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        try:
            # REQ-PRIV-03: zero the content of replaced/deleted rows (e.g. a
            # deleted per-person audit key) instead of leaving it in freelist
            # pages, and VACUUM after a key deletion to reclaim the pages.
            conn.execute("PRAGMA secure_delete=ON")
            with conn:
                conn.execute(STATE_TABLE)
                conn.executemany(
                    "INSERT OR REPLACE INTO state(key, value) VALUES (?, ?)",
                    [(key, json.dumps(self.db.get(key), ensure_ascii=False)) for key in ALL_KEYS],
                )
                conn.execute(f"DELETE FROM state WHERE key NOT IN ({','.join('?' * len(ALL_KEYS))})", list(ALL_KEYS))
            if vacuum:
                conn.execute("VACUUM")
        finally:
            conn.close()
        # REQ-TRC-07: anchor the new head after the commit. A failed anchor
        # write is logged and counted inside record_anchor, never raised.
        record_anchor(self)
