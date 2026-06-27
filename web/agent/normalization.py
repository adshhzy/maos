"""Small projection helpers for Agent Service responses."""

from datetime import datetime
from typing import Any


def unwrap_data(value: Any) -> Any:
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value

def response_items(value: Any) -> list[dict[str, Any]]:
    data = unwrap_data(value)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "messages", "runs", "data"):
            nested = data.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [data]
    return []

def select_latest_run(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        return {}
    time_keys = ("completed_at", "ended_at", "started_at", "created_at", "updated_at")
    return max(runs, key=lambda run: max(parse_time(run.get(key)) for key in time_keys))

def compact_task(task: Any) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    return {
        "id": task.get("id"),
        "title": task.get("title") or task.get("name"),
        "status": task.get("status") or task.get("state"),
        "assignee": task.get("assignee") or task.get("agent"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }

def compact_run(run: dict[str, Any]) -> dict[str, Any]:
    if not run:
        return {}
    return {
        "id": run.get("id"),
        "status": run.get("status") or run.get("state"),
        "agent": run.get("agent") or run.get("agent_name"),
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at") or run.get("ended_at"),
    }

def trim_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...<truncated>"

def parse_time(value: Any) -> float:
    if not value:
        return 0.0
    try:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0
