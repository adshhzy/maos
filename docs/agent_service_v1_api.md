# Agent Service and Provider API v1

This document defines the stable runtime boundary for Agent execution.

- **Agent Service HTTP v1** is the microservice API used by MAOS providers to
  create, poll, cancel, resume, inspect events, and fetch artifacts from an
  external Agent runtime facade.
- **Provider API v1** is the internal Python adapter contract used by the
  persistent execution core to plug in simulator, Multica, Hermes, or future
  runtimes such as Codex CLI.

Legacy `/tasks`, `/comments`, `/runs`, and `/messages` endpoints remain
available for Multica compatibility and Web debugging, but MAOS runtime
providers should use `/api/v1/agent-tasks`.

## Capability Discovery

```http
GET /api/v1/capabilities
```

Returns the service-wide API version, supported lifecycle operations, stable
statuses, A2A state mapping, known backend families, and endpoint templates.

```json
{
  "api_version": "agent-service-v1",
  "service": "agent-service",
  "operations": {
    "create": true,
    "poll": true,
    "cancel": true,
    "resume": true,
    "events": true,
    "artifacts": true,
    "continuation": true,
    "native_resume": false,
    "push_callbacks": false
  },
  "statuses": [
    "accepted",
    "working",
    "input_required",
    "completed",
    "failed",
    "cancelled",
    "timed_out"
  ],
  "a2a_states": {
    "accepted": "TASK_STATE_WORKING",
    "working": "TASK_STATE_WORKING",
    "input_required": "TASK_STATE_INPUT_REQUIRED",
    "completed": "TASK_STATE_COMPLETED",
    "failed": "TASK_STATE_FAILED",
    "cancelled": "TASK_STATE_CANCELED",
    "timed_out": "TASK_STATE_FAILED"
  }
}
```

For one task:

```http
GET /api/v1/agent-tasks/{task_id}/capabilities
```

## Create

```http
POST /api/v1/agent-tasks
Content-Type: application/json
```

```json
{
  "idempotency_key": "workflow:node#1:dispatch",
  "agent": {
    "backend": "multica",
    "agent_key": "general_chat",
    "agent_id": null,
    "agent_name": null
  },
  "input": {
    "title": "MAOS node task",
    "instruction": "Agent instruction plus original task input and upstream node results",
    "context": {
      "graph_input": {},
      "dependencies": {}
    }
  },
  "runtime": {
    "mode": "async",
    "timeout_seconds": 86400,
    "context_policy": "provided_context_only",
    "runtime_profile": "lightweight",
    "execution_mode": "lightweight_hermes_oneshot",
    "priority": "medium",
    "status": "in_progress",
    "allow_duplicate": true
  },
  "callback": {
    "url": null,
    "payload": {
      "workflow_id": "task-...",
      "node_id": "node-id",
      "a2a_task_id": "a2a-task-..."
    }
  },
  "metadata": {}
}
```

Returns a stable Agent task projection:

```json
{
  "api_version": "agent-service-v1",
  "task_id": "agent-task-id",
  "external_id": "agent-task-id",
  "backend": "multica",
  "status": "working",
  "state": "TASK_STATE_WORKING",
  "mode": "created",
  "poll_after_seconds": 30,
  "capabilities": {
    "create": true,
    "poll": true,
    "cancel": true,
    "resume": true,
    "events": true,
    "artifacts": true,
    "continuation": true,
    "native_resume": false,
    "push_callbacks": false
  },
  "progress": {
    "phase": "working",
    "message": "Current task summary",
    "percent": null
  },
  "links": {
    "self": "/api/v1/agent-tasks/agent-task-id",
    "cancel": "/api/v1/agent-tasks/agent-task-id/cancel",
    "resume": "/api/v1/agent-tasks/agent-task-id/resume",
    "events": "/api/v1/agent-tasks/agent-task-id/events",
    "artifacts": "/api/v1/agent-tasks/agent-task-id/artifacts"
  }
}
```

## Poll

```http
GET /api/v1/agent-tasks/{task_id}
```

Stable statuses:

- `accepted`
- `working`
- `input_required`
- `completed`
- `failed`
- `cancelled`
- `timed_out`

MAOS state mapping:

- `accepted`, `working` -> `TASK_STATE_WORKING`
- `input_required` -> `TASK_STATE_INPUT_REQUIRED`
- `completed` -> `TASK_STATE_COMPLETED`
- `failed`, `timed_out` -> `TASK_STATE_FAILED`
- `cancelled` -> `TASK_STATE_CANCELED`

When `status` is `input_required`, `input_request` contains the prompt, schema,
request id, and assignee hints that MAOS converts into a human intervention.

## Resume

```http
POST /api/v1/agent-tasks/{task_id}/resume
Content-Type: application/json
```

```json
{
  "request_id": "payment-confirmation",
  "intervention_id": "human-node#1-agent-payment-confirmation",
  "response": {
    "decision": "allow",
    "comment": "Allow 20% advance payment"
  },
  "responder": {
    "id": "u-001",
    "name": "business owner",
    "role": "business_owner"
  }
}
```

Current Multica compatibility behavior appends a structured MAOS comment and
sets the task back to `in_progress`. A native runtime adapter can implement true
in-place resume behind the same endpoint.

## Cancel

```http
POST /api/v1/agent-tasks/{task_id}/cancel
Content-Type: application/json
```

```json
{
  "reason": "workflow_cancelled",
  "requested_by": "maos"
}
```

## Events

```http
GET /api/v1/agent-tasks/{task_id}/events?since=0
Accept: text/event-stream
```

The current implementation streams heartbeat and trace messages from Multica
run messages. Future adapters can emit native events with the same endpoint.

## Artifacts

```http
GET /api/v1/agent-tasks/{task_id}/artifacts
GET /api/v1/agent-tasks/{task_id}/artifacts/{artifact_id}
GET /api/v1/agent-tasks/{task_id}/artifacts/{artifact_id}/content
```

Current compatibility behavior exposes the latest Agent comment as
`final-result` and, when the comment is exactly a JSON object, also exposes
`structured-output`.

## Internal Provider API v1

The Python provider interface lives in
`maos_runtime.a2a_provider_base.AgentRuntimeProvider`.

Required methods:

- `agent_card(node)`: returns the A2A AgentCard for the selected node.
- `capabilities()`: returns backend operation support.
- `create(request)`: creates or idempotently reuses an A2A task.
- `poll(task_id, request)`: performs one short status poll.
- `cancel(task_id, request)`: cancels the external task when supported.
- `resume(task_id, request)`: sends human input back to the external task.
- `events(task_id, request)`: returns a task event snapshot or backend events.
- `artifacts(task_id, request)`: returns artifact descriptors/content known to
  the provider.

Compatibility aliases:

- `send(request)` delegates to `create(request)`.
- `resume_human_response(task_id, request)` delegates to `resume(...)`.

Built-in providers:

- `simulator`: local test provider, callback capable, best-effort local cancel.
- `multica`: real Multica-backed provider through Agent Service HTTP v1.
- `hermes`: direct Hermes one-shot provider through Agent Service HTTP v1.

The workflow activity layer should call the runtime facade in `maos_runtime.a2a`
rather than importing backend modules directly. This keeps future runtimes
pluggable without changing Temporal workflow logic.
