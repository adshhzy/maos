from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


IssueStatus = Literal[
    "backlog",
    "todo",
    "in_progress",
    "in_review",
    "done",
    "blocked",
    "cancelled",
]

IssuePriority = Literal["low", "medium", "high", "urgent"]


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    agent_key: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    priority: IssuePriority | None = "medium"
    status: IssueStatus | None = "todo"
    project_id: str | None = None
    parent_id: str | None = None
    allow_duplicate: bool = True
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)


class TaskUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    agent_key: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    priority: IssuePriority | None = None
    status: IssueStatus | None = None
    project_id: str | None = None
    parent_id: str | None = None


class TaskListResponse(BaseModel):
    data: list[dict[str, Any]]
    total: int | None = None
    has_more: bool | None = None


class CommentCreateRequest(BaseModel):
    content: str = Field(min_length=1)
    parent_id: str | None = None


class CommandEnvelope(BaseModel):
    data: Any
