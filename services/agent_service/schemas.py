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


class AgentDescriptor(BaseModel):
    backend: str = "multica"
    agent_key: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None


class AgentTaskInput(BaseModel):
    title: str = Field(min_length=1)
    instruction: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class AgentTaskRuntime(BaseModel):
    mode: str = "async"
    timeout_seconds: float | None = None
    context_policy: str | None = None
    runtime_profile: str | None = None
    execution_mode: str | None = None
    priority: IssuePriority | None = "medium"
    status: IssueStatus | None = "todo"
    project_id: str | None = None
    parent_id: str | None = None
    allow_duplicate: bool = True


class AgentTaskCallback(BaseModel):
    url: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentTaskCreateRequest(BaseModel):
    idempotency_key: str | None = None
    agent: AgentDescriptor = Field(default_factory=AgentDescriptor)
    input: AgentTaskInput
    runtime: AgentTaskRuntime = Field(default_factory=AgentTaskRuntime)
    callback: AgentTaskCallback | None = None
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)


class AgentTaskResumeRequest(BaseModel):
    request_id: str | None = None
    intervention_id: str | None = None
    response: dict[str, Any] = Field(default_factory=dict)
    responder: dict[str, Any] | str | None = None
    comment: str | None = None


class AgentTaskCancelRequest(BaseModel):
    reason: str | None = None
    requested_by: str | None = "maos"


class AgentTaskCapabilities(BaseModel):
    create: bool = True
    poll: bool = True
    cancel: bool = True
    resume: bool = True
    events: bool = True
    artifacts: bool = True
    continuation: bool = True
    native_resume: bool = False
    push_callbacks: bool = False


class AgentTaskProgress(BaseModel):
    phase: str
    message: str | None = None
    percent: float | None = None


class AgentTaskProjection(BaseModel):
    api_version: str = "agent-service-v1"
    task_id: str | None = None
    external_id: str | None = None
    backend: str | None = None
    status: str
    state: str
    mode: str
    poll_after_seconds: float
    capabilities: AgentTaskCapabilities
    progress: AgentTaskProgress
    input_request: dict[str, Any] | None = None
    created_at: Any = None
    updated_at: Any = None
    raw_status: Any = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    links: dict[str, str] = Field(default_factory=dict)


class AgentServiceCapabilities(BaseModel):
    api_version: str = "agent-service-v1"
    service: str = "agent-service"
    operations: AgentTaskCapabilities
    statuses: list[str]
    a2a_states: dict[str, str]
    backends: list[str]
    endpoints: dict[str, str]
