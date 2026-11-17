"""Match suggestions (REQ-MATCH-02): rank Certified, fully-verified caregivers
for a request on city, language, availability and visit frequency — not only
city. This only suggests; POST /api/requests/{id}/match is unchanged and
still takes the coordinator's explicit choice.
"""
import copy

from .domain import Change, DomainError
from .service import is_cleared, mutation
from .trace import implements

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
CITY_POINTS = 30
LANGUAGE_POINTS = 25
AVAILABILITY_POINTS = 25
CAPACITY_POINTS = 20


def _find_caregiver(store, caregiver_id: str) -> dict:
    for c in store.db["caregivers"]:
        if c["id"] == caregiver_id:
            return c
    raise DomainError(404, "Care-leaver not found.")


def _find_request(store, request_id: str) -> dict:
    for r in store.db["requests"]:
        if r["id"] == request_id:
            return r
    raise DomainError(404, "Request not found.")


@mutation("caregiver.profile_set", "REQ-MATCH-02")
def set_profile(store, caregiver_id: str, languages: list | None = None, availability: dict | None = None, capacity_visits_per_week: int | None = None) -> Change:
    """Set the matching profile: languages spoken, weekly availability and how
    many visits a week the caregiver can take on. Only the fields given are
    changed."""
    c = _find_caregiver(store, caregiver_id)
    if languages is not None:
        if not isinstance(languages, list) or not languages or not all(str(l).strip() for l in languages):
            raise DomainError(400, "Languages must be a non-empty list.")
        languages = [str(l).strip() for l in languages]
    if availability is not None:
        if not isinstance(availability, dict):
            raise DomainError(400, "Availability must give days and hours.")
        days = availability.get("days")
        hours = availability.get("hours")
        if not isinstance(days, list) or not days or any(d not in WEEKDAYS for d in days):
            raise DomainError(400, "Availability days must be weekday names.")
        if not str(hours or "").strip():
            raise DomainError(400, "Availability hours are required.")
        availability = {"days": list(days), "hours": str(hours).strip()}
    if capacity_visits_per_week is not None:
        if not isinstance(capacity_visits_per_week, int) or isinstance(capacity_visits_per_week, bool) or not 0 <= capacity_visits_per_week <= 21:
            raise DomainError(400, "Capacity must be between 0 and 21 visits a week.")
    before = copy.deepcopy(c)
    if languages is not None:
        c["languages"] = languages
    if availability is not None:
        c["availability"] = availability
    if capacity_visits_per_week is not None:
        c["capacityVisitsPerWeek"] = capacity_visits_per_week
    return Change("caregiver", c["id"], before, copy.deepcopy(c), copy.deepcopy({k: v for k, v in c.items() if k != "dateOfBirth"}))


@implements("REQ-MATCH-02")
def suggest_caregivers(store, request_id: str) -> dict:
    """Rank Certified, fully-verified caregivers for a request.

    Scoring (100 total): city 30, language 25, availability 25, capacity 20.
    Availability is scored on how many days a week the caregiver is free
    against the visits a week the request needs — the request does not name
    days, so day-of-week overlap cannot be scored yet (see SPEC section 8).
    A request with no language preference gives every candidate the language
    points. A caregiver with no capacity recorded scores 0 on capacity and
    the breakdown says so, rather than assuming they can take the visits.
    """
    r = _find_request(store, request_id)
    needed = r["visitsPerWeek"]
    wanted_language = r.get("language")
    suggestions = []
    for c in store.db["caregivers"]:
        if c.get("stage") != "Certified" or not is_cleared(c):
            continue
        city_matched = c.get("city") == r.get("city")
        city_points = CITY_POINTS if city_matched else 0
        if wanted_language is None:
            language_matched, language_points, language_note = True, LANGUAGE_POINTS, "No language preference on this request."
        elif wanted_language in (c.get("languages") or []):
            language_matched, language_points, language_note = True, LANGUAGE_POINTS, None
        else:
            language_matched, language_points, language_note = False, 0, None
        days = len((c.get("availability") or {}).get("days") or [])
        availability_points = AVAILABILITY_POINTS if days >= needed else round(AVAILABILITY_POINTS * days / needed)
        capacity = c.get("capacityVisitsPerWeek")
        if capacity is None:
            capacity_points, capacity_note = 0, "Capacity not set."
        elif capacity >= needed:
            capacity_points, capacity_note = CAPACITY_POINTS, None
        else:
            capacity_points, capacity_note = round(CAPACITY_POINTS * capacity / needed), None
        suggestions.append({
            "caregiverId": c["id"],
            "name": c["name"],
            "city": c["city"],
            "languages": list(c.get("languages") or []),
            "availability": copy.deepcopy(c.get("availability") or {"days": [], "hours": ""}),
            "capacityVisitsPerWeek": capacity,
            "score": city_points + language_points + availability_points + capacity_points,
            "breakdown": {
                "city": {"matched": city_matched, "points": city_points},
                "language": {"matched": language_matched, "points": language_points, "note": language_note},
                "availability": {"daysAvailable": days, "needed": needed, "points": availability_points},
                "capacity": {"capacity": capacity, "needed": needed, "points": capacity_points, "note": capacity_note},
            },
        })
    suggestions.sort(key=lambda s: (-s["score"], s["name"]))
    return {"requestId": r["id"], "suggestions": suggestions}
