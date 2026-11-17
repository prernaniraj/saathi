"""Reports (REQ-REP-01): a read-only summary for the coordinator.

Definitions (also in SPEC.md section 8):
- Visit completion: visits logged vs expected. Expected, per Matched request,
  is visitsPerWeek x full weeks since the request was matched (``matchedAt``,
  set when the match is made). A request matched less than a week ago expects
  0 visits, so the completion rate is null until there is something to
  compare against.
- Punctuality: the share of visits with both a check-in and a check-out, and
  the share of checked-in visits that were late (check-in more than 30
  minutes after the planned time, per REQ-EVV-01).
- Flagged visits: the count of visits with mood "Needs attention".
- Retention: of the caregivers who have ever been matched, the share still in
  the Matched stage. "Ever matched" is the caregivers currently Matched, plus
  any caregiver linked to a Matched request, plus any caregiver named in a
  request.matched audit event — so a caregiver who was matched and later left
  the Matched stage still counts in the denominator.
- Placement rate: Matched requests / all requests.
"""
from datetime import date, datetime, timezone

from .evv import is_late
from .trace import implements


@implements("REQ-REP-01")
def summary(store, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    db = store.db
    requests = db.get("requests", [])
    visits = db.get("visits", [])
    caregivers = db.get("caregivers", [])
    matched_requests = [r for r in requests if r.get("status") == "Matched"]

    expected = 0
    for r in matched_requests:
        matched_at = r.get("matchedAt")
        if not matched_at:
            continue
        try:
            weeks = (today - date.fromisoformat(matched_at)).days // 7
        except ValueError:
            continue
        expected += r["visitsPerWeek"] * max(weeks, 0)
    logged = len(visits)
    completion_rate = (logged / expected) if expected else None

    with_both = [v for v in visits if v.get("checkinAt") and v.get("checkoutAt")]
    check_in_out_rate = (len(with_both) / len(visits)) if visits else None
    checked_in = [v for v in visits if v.get("checkinAt") and v.get("plannedAt")]
    late = [v for v in checked_in if is_late(v)]
    late_rate = (len(late) / len(checked_in)) if checked_in else None

    flagged = sum(1 for v in visits if v.get("mood") == "Needs attention")

    ever_matched = {c["id"] for c in caregivers if c.get("stage") == "Matched"}
    ever_matched |= {r["caregiverId"] for r in matched_requests if r.get("caregiverId")}
    for e in db.get("audit", []):
        if e.get("action") == "request.matched":
            after = e.get("after") or {}
            caregiver = after.get("caregiver") if isinstance(after, dict) else None
            if isinstance(caregiver, dict) and caregiver.get("id"):
                ever_matched.add(caregiver["id"])
    currently_matched = {c["id"] for c in caregivers if c.get("stage") == "Matched"}
    retention_rate = (len(currently_matched & ever_matched) / len(ever_matched)) if ever_matched else None

    placement_rate = (len(matched_requests) / len(requests)) if requests else None

    return {
        "asOf": today.isoformat(),
        "visits": {"logged": logged, "expected": expected, "completionRate": completion_rate},
        "punctuality": {
            "total": len(visits),
            "withCheckInAndCheckOut": len(with_both),
            "checkInOutRate": check_in_out_rate,
            "checkedIn": len(checked_in),
            "late": len(late),
            "lateRate": late_rate,
        },
        "flaggedVisits": flagged,
        "retention": {"everMatched": len(ever_matched), "currentlyMatched": len(currently_matched & ever_matched), "rate": retention_rate},
        "placement": {"matchedRequests": len(matched_requests), "totalRequests": len(requests), "rate": placement_rate},
    }
