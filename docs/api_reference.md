# MAOS Persistent Execution Core API Reference

This document summarizes the current API surface of the persistent multi-Agent
execution core.

The project exposes three API layers:

- **Sandbox API**: the main control-plane HTTP API used by Web UI and external
  clients to create, monitor, inspect, export, and signal workflow runs.
- **Agent Service API v1**: the stable HTTP facade used by MAOS providers when a
  node is backed by an external Agent runtime such as Multica or Hermes.
- **Provider Runtime API v1**: the internal Python API that lets the Temporal
  workflow/activity layer dispatch nodes to simulator, CLI, evaluator, or
  external Agent backends.

## Service Ports

Default local ports:

| Service | Default URL | Purpose |
| --- | --- | --- |
| Web UI | `http://127.0.0.1:8765` | Browser dashboard and graph visualization |
| Sandbox API | `http://127.0.0.1:8766` | Persistent execution control plane |
| Simulator API | `http://127.0.0.1:8767` | Local simulated Agent execution |
| Agent Service API | `http://127.0.0.1:8091` | Multica/Hermes facade for real Agent tasks |
| Temporal frontend | `127.0.0.1:7233` | Temporal workflow service |
| Temporal UI | `http://127.0.0.1:8233` | Temporal browser UI, when enabled |

## Web UI HTTP Surface

Base URL: `http://127.0.0.1:8765`

The Web UI process is a lightweight `http.server` dashboard. It does not own
execution state. It serves static assets and proxies dashboard API requests to
the Sandbox API service.

| Method | Path | Description |
| --- | --- | --- |
| `GET`, `HEAD` | `/` | Dashboard HTML |
| `GET`, `HEAD` | `/index.html` | Dashboard HTML |
| `GET` | `/config.js` | Runtime UI config; currently exposes `sandboxApiBase` |
| `GET` | `/static/{path}` | Static dashboard assets |
| `GET` | `/favicon.ico` | Empty favicon response |
| `GET`, `POST` | `/api/*` | Transparent proxy to the Sandbox API service |
| `GET` | `/state`, `/examples`, `/example`, `/agent-trace`, `/agent-input` | Compatibility proxy paths |
| `POST` | `/run-batch` | Compatibility proxy path |
| `OPTIONS` | `*` | CORS preflight response |

If `auth_username` and `auth_password` are configured when starting the Web UI
service, all Web UI paths require HTTP Basic authentication.

## Sandbox API

Base URL: `http://127.0.0.1:8766`

OpenAPI UI: `GET /docs`

### System

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Service root with docs and health links |
| `GET` | `/api/health` | Service health, Temporal runtime info, simulator URL, Agent Service URL, Execution Store path |
| `GET` | `/health` | Compatibility alias for `/api/health` |
| `GET` | `/api/provider-status` | Provider runtime status; currently includes Codex warm-up/runtime-pool state |

### Task Lifecycle

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/tasks` | List task rows for the dashboard, merged from live runtime and Execution Store |
| `GET` | `/state` | Compatibility alias for `/api/tasks` |
| `POST` | `/api/tasks` | Create one or more workflow tasks from JSON task graphs |
| `POST` | `/run-batch` | Compatibility alias for `/api/tasks` |
| `POST` | `/api/preview` | Validate and preview task graph execution state without starting workflows |
| `GET` | `/api/tasks/{task_id}` | Get one projected task detail view |
| `POST` | `/api/tasks/{task_id}/export-markdown` | Export final outputs or one node output to Markdown |
| `POST` | `/api/tasks/{task_id}/export-replay` | Generate replay frames and an animated replay artifact from completed task history |

Create request:

```json
{
  "graphs": [
    {
      "id": "graph-id",
      "name": "Graph name",
      "input": {},
      "nodes": [],
      "edges": []
    }
  ]
}
```

For backward compatibility, the body may also be a bare array of graph objects.

Create response:

```json
{
  "ok": true,
  "task_ids": ["task-..."]
}
```

Markdown export request:

```json
{
  "output_dir": "D:/path/to/output",
  "node_id": "optional_node_id"
}
```

Replay export request:

```json
{
  "output_dir": "D:/path/to/output",
  "frame_duration_ms": 900
}
```

### Execution Store

Execution Store is the primary persisted read model for active, completed, and
archived workflow results. Task rows and task details are rebuilt from
structured store projections; raw snapshots are kept only as compatibility and
audit/debug fallback.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/execution-store/workflows?limit=100` | List persisted workflow runs |
| `GET` | `/api/execution-store/workflows/{workflow_id}` | Load one persisted workflow task projection |

### Agent Callbacks and Events

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/agent-callbacks` | Callback endpoint for simulator or Agent runtime events |
| `POST` | `/api/v1/agent-events` | Stable alias for Agent runtime events |

Callback payloads are dictionaries containing workflow/task identifiers and a
status such as `working`, `completed`, `failed`, or `needs_input`. The workflow
manager converts callbacks into Temporal workflow signals/events.

### Human-In-The-Loop

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/human-interventions?task_id=&status=` | List human interventions globally, optionally filtered |
| `GET` | `/api/tasks/{task_id}/human-interventions?status=` | List interventions for one workflow |
| `POST` | `/api/tasks/{task_id}/human-interventions/{intervention_id}/responses` | Resolve a pending human intervention |

Human response request:

```json
{
  "responder": "operator",
  "decision": "approved",
  "comment": "Approved with rollout guardrail.",
  "response": {
    "decision": "approved",
    "comment": "Approved with rollout guardrail."
  }
}
```

The sandbox sends the corresponding Temporal signal and, for Agent-requested
human input, resumes the original Agent task through the provider API.

### Examples

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/examples` | List bundled task graph examples |
| `GET` | `/examples` | Compatibility alias for `/api/examples` |
| `GET` | `/api/example?name={file.json}` | Load one bundled example graph |
| `GET` | `/example?name={file.json}` | Compatibility alias for `/api/example` |

### Agent Debug Views

These endpoints support the Web UI detail panes.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/agent-trace?task_id={a2a_task_id}` | Build readable trace for an Agent task |
| `GET` | `/agent-trace?task_id=...` | Compatibility alias |
| `GET` | `/api/agent-input?task_id={a2a_task_id}` | Show Agent Service input/projection for a task |
| `GET` | `/agent-input?task_id=...` | Compatibility alias |
| `GET` | `/api/local-runtime-input?task_id={a2a_task_id}&backend={backend}` | Show local CLI/evaluator provider prompt/input |
| `GET` | `/local-runtime-input?...` | Compatibility alias |
| `GET` | `/api/local-runtime-output?task_id={a2a_task_id}&backend={backend}` | Show local CLI/evaluator provider output |
| `GET` | `/local-runtime-output?...` | Compatibility alias |

### Artifact Store

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/artifacts/{artifact_ref}` | Artifact metadata without full content |
| `GET` | `/api/artifacts/{artifact_ref}/content` | Artifact metadata plus full content |

`/content` returns:

```json
{
  "ok": true,
  "artifact_ref": "sha256-...",
  "content_hash": "sha256:...",
  "mime_type": "application/json",
  "size": 12345,
  "content": "..."
}
```

## Agent Service API v1

Base URL: `http://127.0.0.1:8091`

This is the stable external-Agent facade consumed by MAOS providers. Current
implementation is backed by Multica and a lightweight Hermes mode.

### Discovery

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Agent Service health and Multica daemon status |
| `GET` | `/runtimes` | Runtime/daemon status envelope |
| `GET` | `/agents?include_archived=false` | List available Multica agents |
| `GET` | `/agent-presets` | List configured MAOS agent presets |
| `GET` | `/api/v1/capabilities` | Stable v1 capability document |

### Stable Agent Task v1

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/v1/agent-tasks` | Create or idempotently reuse an Agent task |
| `GET` | `/api/v1/agent-tasks/{task_id}` | Poll one Agent task projection |
| `GET` | `/api/v1/agent-tasks/{task_id}/capabilities` | Get task-specific capabilities |
| `POST` | `/api/v1/agent-tasks/{task_id}/cancel` | Cancel an Agent task |
| `POST` | `/api/v1/agent-tasks/{task_id}/resume` | Resume a task after human input |
| `GET` | `/api/v1/agent-tasks/{task_id}/events?since=0&poll_seconds=30` | Server-sent trace/heartbeat stream |
| `GET` | `/api/v1/agent-tasks/{task_id}/artifacts` | List task artifacts |
| `GET` | `/api/v1/agent-tasks/{task_id}/artifacts/{artifact_id}` | Get one artifact descriptor, with content when available |
| `GET` | `/api/v1/agent-tasks/{task_id}/artifacts/{artifact_id}/content` | Get artifact content |

Create request:

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
    "context": {}
  },
  "runtime": {
    "mode": "async",
    "timeout_seconds": 86400,
    "context_policy": "provided_context_only",
    "runtime_profile": "lightweight",
    "execution_mode": "lightweight_hermes_oneshot",
    "priority": "medium",
    "status": "in_progress",
    "project_id": null,
    "parent_id": null,
    "allow_duplicate": true
  },
  "callback": {
    "url": null,
    "payload": {}
  },
  "metadata": {}
}
```

Stable task projection:

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
  "input_request": null,
  "metadata": {},
  "links": {}
}
```

Stable status mapping:

| Agent Service status | A2A task state |
| --- | --- |
| `accepted` | `TASK_STATE_WORKING` |
| `working` | `TASK_STATE_WORKING` |
| `input_required` | `TASK_STATE_INPUT_REQUIRED` |
| `completed` | `TASK_STATE_COMPLETED` |
| `failed` | `TASK_STATE_FAILED` |
| `cancelled` | `TASK_STATE_CANCELED` |
| `timed_out` | `TASK_STATE_FAILED` |

Resume request:

```json
{
  "request_id": "payment-confirmation",
  "intervention_id": "human-node#1-agent-payment-confirmation",
  "response": {
    "decision": "approved",
    "comment": "Approved."
  },
  "responder": {
    "id": "u-001",
    "name": "operator"
  },
  "comment": "Approved."
}
```

Cancel request:

```json
{
  "reason": "workflow_cancelled",
  "requested_by": "maos"
}
```

### Legacy Multica Compatibility Endpoints

These endpoints remain for debugging and compatibility. New MAOS providers
should prefer `/api/v1/agent-tasks`.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/tasks?status=&assignee_id=&assignee_name=&priority=&limit=50&offset=0` | List Multica tasks |
| `POST` | `/tasks` | Create a Multica-compatible task |
| `GET` | `/tasks/{task_id}` | Get one task |
| `PATCH` | `/tasks/{task_id}` | Update task fields |
| `POST` | `/tasks/{task_id}/status/{status}` | Set task status |
| `GET` | `/tasks/{task_id}/comments?recent=&roots_only=&since=&summary=` | List comments |
| `POST` | `/tasks/{task_id}/comments` | Add comment |
| `GET` | `/tasks/{task_id}/runs` | List task runs |
| `GET` | `/tasks/{task_id}/metadata` | List metadata |
| `GET` | `/runs/{run_id}/messages?issue_id=&since=` | List run messages |
| `GET` | `/tasks/{task_id}/events?poll_seconds=30` | Legacy SSE message stream |

## Simulator API

Base URL: `http://127.0.0.1:8767`

The simulator API is a local test/runtime service used by simulator-backed graph
nodes. Normal users usually interact with it indirectly through Sandbox API.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/simulator/health` | Simulator health check |
| `POST` | `/api/simulator/jobs` | Start a simulated node job |
| `GET` | `/api/simulator/jobs/{job_id}` | Get simulated job status |
| `POST` | `/api/simulator/jobs/{job_id}/human-responses` | Resume a simulator job after human input |

Create job request:

```json
{
  "node": {},
  "dependency_results": {},
  "graph_input": {},
  "callback": {
    "url": "http://127.0.0.1:8766/api/agent-callbacks",
    "payload": {}
  },
  "job_id": "optional-stable-job-id"
}
```

## Provider Runtime API v1

Provider Runtime API is internal Python API, not HTTP. Workflow activities call
the facade functions in `maos_runtime.a2a`.

### Facade Functions

| Function | Description |
| --- | --- |
| `register_agent_provider(provider, aliases=(), replace=False)` | Register an Agent backend provider |
| `registered_agent_backends()` | List registered backend names |
| `provider_capabilities(backend=None)` | Get all or one provider capability document |
| `get_agent_card(node)` | Get an A2A AgentCard for a graph node |
| `send_message(request)` | Idempotently create an A2A task for a node |
| `poll_task(request)` | Perform one short poll for an A2A task |
| `cancel_task(request)` | Cancel an A2A task |
| `resume_task_with_human_response(request)` | Resume an Agent task after human input |
| `task_events(request)` | Return provider events/task snapshot |
| `task_artifacts(request)` | Return provider artifacts |
| `get_task(request)` | Return the latest task projection |
| `complete_task_from_agent_callback(callback)` | Convert callback payload into an A2A event |
| `extract_result_from_task(task)` | Extract completed `dag-node-result` payload |

### Provider Class Contract

All providers inherit `ProviderRuntime` and implement:

```python
def agent_card(self, node: dict[str, Any]) -> dict[str, Any]: ...
def start_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult: ...
def inspect_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult: ...
```

Optional operations:

```python
supports_cancel = True
supports_resume = True
supports_push_callbacks = True
def _cancel(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]: ...
def _resume(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]: ...
```

`ProviderRuntime.create()` and `ProviderRuntime.poll()` are final lifecycle entry
points for normal use. Providers should not implement legacy `_create()` or
`_poll()` compatibility paths.

Built-in backends:

| Backend | Provider | Notes |
| --- | --- | --- |
| `simulator` | `SimulatorProvider` | Local test/simulation provider, supports callbacks and resume |
| `multica` | `MulticaProvider` | External Multica Agent Service provider |
| `hermes` | `HermesOneshotProvider` | Direct Hermes one-shot provider through Agent Service facade |
| `codex` | `CodexCliProvider` | Local Codex CLI provider |
| `claude` | `ClaudeCliProvider` | Local Claude CLI provider |
| `claude-huawei` | `ClaudeHuaweiCliProvider` | Local Claude CLI routed to Huawei Cloud DeepSeek |
| `evaluator` | `EvaluatorProvider` | Deterministic local evaluator provider |

## Task Graph JSON API

Task graph JSON is the declarative API used by clients to define workflows. The
formal schema lives in `schemas/task_graph.schema.json`.

Related docs:

- `docs/task_graph_json_format.md`
- `docs/task_graph_json_schema.md`

Minimum shape:

```json
{
  "id": "task-id",
  "name": "Task name",
  "input": {},
  "nodes": [
    {
      "id": "node_id",
      "type": "agent",
      "deps": [],
      "prompt": "Do the work",
      "agent": {
        "backend": "claude"
      }
    }
  ],
  "edges": []
}
```

Common node types:

| Type | Purpose |
| --- | --- |
| `agent` | Dispatch work to a provider-backed Agent |
| `simulator` | Simulated execution |
| `human` | Planned human-in-the-loop step |
| `decision` / control-flow gate | Route execution by structured result |
| `evaluator` | Deterministic evaluation node |

Control-flow graphs support normal dependency edges, conditional branches, and
bounded loops. Graph validation happens before workflow submission.

## Artifact Transfer API

Dependency artifacts can be passed by reference or inline.

Environment switch:

```text
A2A_DEPENDENCY_ARTIFACT_MODE=ref
A2A_DEPENDENCY_ARTIFACT_MODE=inline
```

Node/agent-level switch:

```json
{
  "agent": {
    "backend": "claude",
    "artifact_transfer_mode": "ref"
  }
}
```

In `ref` mode, downstream A2A messages carry:

```json
{
  "artifact_ref": "sha256-...",
  "uri": "http://127.0.0.1:8766/api/artifacts/sha256-.../content",
  "content_hash": "sha256:...",
  "mime_type": "application/json",
  "size": 12345,
  "summary": "Upstream result summary"
}
```

In `inline` mode, downstream messages include compact business content directly.

## Related Documents

- `docs/agent_service_v1_api.md`
- `docs/artifact_external_storage.md`
- `docs/human_in_loop_api.md`
- `docs/provider_runtime_lifecycle.md`
- `docs/task_graph_json_format.md`
- `docs/task_graph_json_schema.md`
- `docs/unified_execution_store.md`
