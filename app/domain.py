from dataclasses import dataclass
from typing import Any

STAGES = ["Applied", "Training", "Certified", "Matched"]
NOTES = ["Referred by a care home", "Five training modules", "Ready to match", "Visiting a family"]
MODULES = [
    "Safety and hygiene",
    "Companionship and conversation",
    "Dementia basics",
    "Medication reminders and emergencies",
    "Reporting and ethics",
]
MOODS = ["Good", "Okay", "Needs attention"]
VERIFICATION = [("identity", "Identity document"), ("background", "Background check"), ("references", "References")]
VERIFICATION_STATUSES = ["Pending", "Verified", "Failed"]


class DomainError(Exception):
    """A business-rule failure that maps to an HTTP status (400, 404 or 409)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Ctx:
    """Who and what is behind a change. Carried into every audit event and log line."""

    request_id: str
    trace_id: str
    actor: str = "anonymous"


@dataclass
class Change:
    """What a mutation did, so the audit log can record it."""

    entity_type: str
    entity_id: str
    before: Any
    after: Any
    result: Any
