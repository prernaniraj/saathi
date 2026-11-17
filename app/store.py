import json
import os
import threading
from pathlib import Path

from .audit import AuditLog
from .seed import seed
from .trace import implements


class Store:
    """JSON-file store. Pass no path for an in-memory store (tests).

    A corrupt file raises instead of re-seeding, so audit history is never silently replaced.
    """

    @implements("REQ-TRC-06")
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.lock = threading.RLock()
        self.db = None
        if self.path and self.path.exists():
            try:
                self.db = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise RuntimeError(f"Cannot read {self.path}: {e}. Fix or move the file; it was not overwritten.") from e
        if self.db is None:
            self.db = seed()
            self.save()

    @property
    def audit(self) -> AuditLog:
        return AuditLog(self.db["audit"])

    def next_id(self, prefix: str) -> str:
        self.db["seq"] += 1
        return f"{prefix}{self.db['seq'] - 1}"

    @implements("REQ-TRC-06")
    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.db, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
