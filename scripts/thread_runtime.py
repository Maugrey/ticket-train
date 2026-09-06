"""Parse product task snapshots; a manifest RUNNING flag is not liveness proof."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

MAX_AGE_SECONDS = 120


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("runtime observation time requires a timezone")
    return parsed


def parse_wait_result(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept raw wait_threads output or its MCP text/structured wrapper.

    A multi-target wait may return only one poll. Omitted targets are NOT
    finished, missing, or safe to restart. Never infer them from the wake item.
    """
    if raw.get("isError"):
        raise ValueError("product task observation failed; do not relaunch")
    if "polls" not in raw:
        if isinstance(raw.get("structuredContent"), dict):
            raw = raw["structuredContent"]
        else:
            blocks = [x for x in raw.get("content", []) if x.get("type") == "text"]
            if len(blocks) != 1:
                raise ValueError("expected one raw wait_threads result")
            raw = json.loads(blocks[0]["text"])
    polls = raw.get("polls")
    if not isinstance(polls, list) or not polls:
        raise ValueError("no observed tasks; preserve existing phase identities")
    result = []
    seen = set()
    for poll in polls:
        task = poll.get("thread") or {}
        turn = poll.get("latestTurn") or {}
        task_id = task.get("id")
        if not task_id:
            raise ValueError("invalid product snapshot: expected polls[].thread.id and polls[].latestTurn; save the raw wait_threads return without flattening, summarizing or inventing fields")
        if task_id in seen:
            raise ValueError("duplicate observed task ID; capture each target once")
        seen.add(task_id)
        task_status = (task.get("status") or {}).get("type")
        flags = (task.get("status") or {}).get("activeFlags") or []
        turn_status = turn.get("status")
        status = "unknown"
        if task_status in {"waitingOnApproval", "waitingOnUserInput"} or any(x in flags for x in ("waitingOnApproval", "waitingOnUserInput")):
            status = "needs_input"
        elif task_status in {"active", "running", "busy"} and turn_status == "inProgress":
            status = "running"
        elif task_status == "idle" and turn_status in {"completed", "failed", "interrupted"}:
            status = turn_status
        result.append({
            "thread_id": task_id, "host_id": task.get("hostId"),
            "runtime_status": status, "product_status": task_status,
            "turn_id": turn.get("id"), "turn_status": turn_status,
            "cursor": poll.get("cursor"),
        })
    return result


def phase_observation(value: dict[str, Any], owner: str | None) -> tuple[str, str | None]:
    observation = value.get("runtime_observation") or {}
    if observation.get("thread_id") != value.get("thread_id") or observation.get("owner_thread_id") != owner:
        return "unobserved", "no runtime observation for the current task and owner"
    status = observation.get("runtime_status", "unknown")
    # Terminal evidence stays actionable until its envelope is consumed. Time
    # cannot transform a finished child back into a running one.
    if status in {"completed", "failed", "interrupted", "needs_input"}:
        return status, "observed task outcome has not been reconciled"
    try:
        age = (datetime.now(timezone.utc) - timestamp(observation["observed_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return "unobserved", "invalid runtime observation timestamp"
    if not 0 <= age <= MAX_AGE_SECONDS:
        return "stale", "runtime observation is stale"
    if status != "running":
        return "unknown", "product task status is unknown; inspect, never blindly relaunch"
    return "running", None
