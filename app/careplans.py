"""Care plans (REQ-PLAN-01): one plan per request, with a task checklist per
visit. The plan lives on the request dict as ``carePlan`` ({goals, tasks}), so
it travels with the request in get_state: the matched caregiver and the family
see the plan for their own request, and the coordinator sees all of them.
"""
import copy

from .domain import Change, DomainError
from .service import mutation
from .trace import implements


def _find_request(store, request_id: str) -> dict:
    for r in store.db["requests"]:
        if r["id"] == request_id:
            return r
    raise DomainError(404, "Request not found.")


def _plan(request: dict) -> dict:
    return request.setdefault("carePlan", {"goals": "", "tasks": []})


@implements("REQ-PLAN-01")
def get_care_plan(store, request_id: str) -> dict | None:
    """The care plan for a request, or None when none has been set."""
    r = _find_request(store, request_id)
    plan = r.get("carePlan")
    return copy.deepcopy(plan) if plan else None


@mutation("request.care_plan_set", "REQ-PLAN-01")
def set_care_plan(store, request_id: str, goals: str) -> Change:
    """Create or replace the plan's goals. Existing tasks are kept."""
    r = _find_request(store, request_id)
    if not str(goals or "").strip():
        raise DomainError(400, "Care plan goals are required.")
    before = copy.deepcopy(r)
    plan = _plan(r)
    plan["goals"] = str(goals).strip()
    return Change("request", r["id"], before, copy.deepcopy(r), copy.deepcopy(r["carePlan"]))


@mutation("request.care_plan_task_added", "REQ-PLAN-01")
def add_task(store, request_id: str, title: str) -> Change:
    """Add a task to the checklist. Adding the first task creates an empty
    plan (goals blank) so the caregiver can see the list before the goals
    are written."""
    r = _find_request(store, request_id)
    if not str(title or "").strip():
        raise DomainError(400, "Task title is required.")
    before = copy.deepcopy(r)
    plan = _plan(r)
    task = {"id": store.next_id("t"), "title": str(title).strip(), "done": False}
    plan.setdefault("tasks", []).append(task)
    return Change("request", r["id"], before, copy.deepcopy(r), copy.deepcopy(task))


@mutation("request.care_plan_task_toggled", "REQ-PLAN-01")
def toggle_task(store, request_id: str, task_id: str) -> Change:
    """Flip a task between done and not done."""
    r = _find_request(store, request_id)
    plan = r.get("carePlan")
    if not plan:
        raise DomainError(404, "Care plan not found.")
    task = next((t for t in plan.get("tasks", []) if t["id"] == task_id), None)
    if task is None:
        raise DomainError(404, "Task not found.")
    before = copy.deepcopy(r)
    task["done"] = not task["done"]
    return Change("request", r["id"], before, copy.deepcopy(r), copy.deepcopy(task))
