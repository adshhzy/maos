"""Workflow identifier helpers for sandbox tasks."""

import re
import uuid
from typing import Any


def _task_id_for_graph(graph: dict[str, Any]) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", graph["id"]).strip("-") or "graph"
    return f"task-{slug}-{uuid.uuid4()}"
