# Temporal Execution Core

This project contains Temporal Python SDK examples. The main example is a
generic JSON control-flow graph interpreter for a persistent multi-Agent
execution sandbox: it reads one or more graphs from JSON, starts one Temporal
workflow per task, schedules runnable Agent nodes by dependency/control edges,
executes ready nodes in parallel or with graph-level concurrency limits,
supports conditional branches and bounded loops, and wakes workflows from
simulator callbacks, Agent Service API v1 polling, or local provider runtime
completion.

## Run

```powershell
cd D:\dev\MAOS\temporal_execution_core
.\.venv\Scripts\python.exe run_workflow.py
```

Expected output:

```text
Hello, MAOS! Temporal workflow completed successfully.
```

## Run a JSON Control-Flow Graph

Start the sandbox service first, then submit a graph through its API from
another terminal:

```powershell
cd D:\dev\MAOS\temporal_execution_core
.\.venv\Scripts\python.exe run_dag.py examples\order_processing.json
.\.venv\Scripts\python.exe run_dag.py examples\content_pipeline.json
.\.venv\Scripts\python.exe run_dag.py examples\control_flow_review_loop.json
```

## Visualize a JSON Control-Flow Graph

```powershell
cd D:\dev\MAOS\temporal_execution_core
.\.venv\Scripts\python.exe visualize_execution.py examples\order_processing.json
.\.venv\Scripts\python.exe visualize_execution.py examples\content_pipeline.json
```

If no JSON file is provided, the visualizer uses `examples/order_processing.json`.

## Run the persistent execution sandbox microservice

```powershell
cd D:\dev\MAOS\temporal_execution_core
.\.venv\Scripts\python.exe sandbox_service.py
```

Then open:

```text
http://127.0.0.1:8765
```

This starts two local HTTP microservices and an embedded persistent Temporal
dev server:

```text
Web UI:              http://127.0.0.1:8765
Sandbox API:         http://127.0.0.1:8766
Sandbox API docs:    http://127.0.0.1:8766/docs
Simulator API:        http://127.0.0.1:8767
Temporal Server:      127.0.0.1:7233
Temporal UI:          http://127.0.0.1:8233
Temporal DB file:     D:\dev\MAOS\temporal-data\temporal.db
```

`sandbox_service.py` is kept as the all-in-one development entrypoint. It starts
the Web/API process, simulator, embedded Temporal server, and Temporal worker in
one Python process.

For a split-process deployment, start the services separately:

```powershell
cd D:\dev\MAOS\temporal_execution_core

# Terminal 1: Temporal server + worker
.\.venv\Scripts\python.exe worker_service.py

# Terminal 2: simulator API
.\.venv\Scripts\python.exe simulator_service.py

# Terminal 3: Sandbox API only
.\.venv\Scripts\python.exe sandbox_api_service.py --port 8766 --temporal-address 127.0.0.1:7233

# Terminal 4: Web UI only
.\.venv\Scripts\python.exe web_ui_service.py --port 8765 --sandbox-api-base http://127.0.0.1:8766
```

In split-process mode, the Web UI is only a browser client and local API proxy.
The FastAPI sandbox API creates workflows, queries Temporal, and sends workflow
signals. The worker process owns workflow/activity execution.

The sandbox can also connect to the Agent Service facade used by external
Agent backends such as Multica and Hermes:

```text
Agent Service API:    http://127.0.0.1:8091
```

The Agent Service facade now lives inside this repository. Start it from this
project when `backend=multica` or `backend=hermes` nodes are needed:

```powershell
cd D:\dev\MAOS\temporal_execution_core
.\.venv\Scripts\python.exe -m uvicorn services.agent_service.main:app --host 127.0.0.1 --port 8091
```

Override the service URL if needed:

```powershell
$env:AGENT_SERVICE_API_BASE = "http://127.0.0.1:8091"
```

By default the sandbox starts an embedded Temporal dev server with a persistent
SQLite database file at `D:\dev\MAOS\temporal-data\temporal.db`. Completed
workflow history and state are available after restarting the sandbox service.

The Web UI is only a client of the sandbox API. From the browser you can:

- Select multiple local `.json` control-flow graph files.
- Select multiple bundled examples from the dropdown.
- Click `Run Loaded Batch` to start all selected graphs in parallel.
- Switch between task tabs to inspect each graph's state.

To run another server instance on a different port:

```powershell
.\.venv\Scripts\python.exe sandbox_service.py --port 8766
```

To use a different local persistent Temporal DB:

```powershell
.\.venv\Scripts\python.exe sandbox_service.py `
  --temporal-db-file D:\dev\MAOS\temporal-data\another-temporal.db
```

To connect to an already running Temporal server instead of starting the
embedded dev server:

```powershell
.\.venv\Scripts\python.exe sandbox_service.py `
  --temporal-address 127.0.0.1:7233
```

The web UI draws each control-flow graph as an SVG graph, colors nodes by
execution status, shows dependency/control edges, lets you select a node for
details, and refreshes `/api/tasks` every 30 seconds. Use `Refresh Now` to
trigger an immediate on-demand refresh. The Sandbox API uses the SQLite
Execution Store as the primary read model and performs best-effort live
Temporal refreshes while workflows are still running.

The `All Tasks Board` above the graph summarizes the whole batch:

- Total, running, suspended, completed, and failed workflows.
- Total active activities and aggregate heartbeat count.
- Per-task workflow status and workflow id.
- Per-task node state breakdown.
- Current running or suspended activity nodes.
- Simulator elapsed/planned time and latest heartbeat timestamp.
- Click any board row or tab to switch the graph view to that task.

## Sandbox microservice API

Full current API reference:

```text
docs/api_reference.md
```

The canonical OpenAPI UI is available when the Sandbox API is running:

```text
http://127.0.0.1:8766/docs
```

Create one or more control-flow graph tasks:

```http
POST /api/tasks
Content-Type: application/json

{
  "graphs": [
    { "id": "graph-id", "name": "Graph", "input": {}, "nodes": [] }
  ]
}
```

Response:

```json
{
  "ok": true,
  "task_ids": ["task-..."]
}
```

Query all sandbox tasks:

```http
GET /api/tasks
```

Query one sandbox task:

```http
GET /api/tasks/{task_id}
```

Mock Agent completion callback:

```http
POST /api/agent-callbacks
```

This endpoint is called by the simulator service. Normal users and UI clients
do not need to call it directly.

List bundled examples:

```http
GET /api/examples
GET /api/example?name=order_processing.json
```

Health:

```http
GET /api/health
```

The simulator is also a microservice:

```http
POST /api/simulator/jobs
GET /api/simulator/health
```

The mock Agent runtime submits jobs to the simulator API. When a job completes,
the simulator calls back into `POST /api/agent-callbacks`; the sandbox service
turns that callback into a Temporal signal for the waiting workflow.

## Agent execution with A2A data transfer

Google's A2A protocol repository is checked out at:

```text
D:\dev\MAOS\A2A
```

Each activity node is modeled as one local Agent invocation. A2A is used for
data transfer between dependent or control-flow-linked Agent nodes. The local adapter in
`maos_runtime.a2a` follows the A2A shape of AgentCard, Message, Part, Task,
TaskStatus, and Artifact:

- Temporal invokes the node provider and gives it an A2A `Message`.
- The A2A message carries upstream dependency data as referenced A2A
  `Artifact` objects from completed parent Agent tasks.
- The Agent immediately returns an A2A `Task` in `TASK_STATE_WORKING`.
- A simulator-backed node posts a callback to the sandbox API when the mock
  work completes.
- Agent Service backed nodes, currently `multica` and `hermes`, are created
  through `POST /api/v1/agent-tasks` on the Agent Service facade. The workflow
  uses durable Temporal timers and short polling activities to query
  `GET /api/v1/agent-tasks/{id}`.
- Local provider nodes, currently `codex`, `claude`, `claude-huawei`, and
  `evaluator`, use the same Provider Runtime API v1 lifecycle:
  `create`, `poll`, `cancel`, `resume`, `events`, and `artifacts`.
- All backends are normalized into completed A2A `Task`/`Artifact` objects.
- The workflow reads the node result from the A2A `Artifact` data part.
- Downstream Agent nodes receive those result artifacts through their own A2A
  input messages.

Temporal activities no longer sleep for the whole node runtime. Instead:

- A short activity invokes the Agent and returns the Agent's A2A task id.
- The workflow sets the node to `suspended`.
- Simulator nodes wait with `workflow.wait_condition(...)` until the callback
  is converted to `agent_node_completed(...)`.
- Polling providers use `workflow.sleep(...)` plus short `poll_agent_node`
  activities. The sleep is durable and does not hold a worker thread.
- No worker thread is held while the Agent is running.
- Callback or poll fields update `backend`, `agent_service_task_id`,
  `agent_status`, `heartbeat_count`, `last_heartbeat_at`, and
  `elapsed_seconds` for the web UI.

By default simulator jobs run for a random duration between 5 and 120 seconds.
Examples can override this with `simulate.min_seconds` and
`simulate.max_seconds`.

## JSON Task Graph Format

Task graphs are the public declarative API for workflow orchestration. The
formal schema is `schemas/task_graph.schema.json`, and the full authoring guide
is `docs/task_graph_json_format.md`.

Minimum shape:

```json
{
  "id": "graph-id",
  "name": "Human readable graph name",
  "graph_type": "control_flow",
  "input": {},
  "start": "first_node",
  "nodes": [
    {
      "id": "first_node",
      "type": "agent",
      "operation": "agent_task",
      "agent": {
        "backend": "simulator"
      }
    }
  ],
  "edges": []
}
```

The historic DAG shorthand still works: omit `edges` and put dependency ids in
each node's `deps`. The interpreter converts those dependencies into
unconditional edges and keeps `join=all`, so old examples continue to run.

Supported built-in backends are:

- `simulator`: local random-duration simulator.
- `multica`: external Multica Agent through Agent Service API v1.
- `hermes`: Hermes lightweight mode through Agent Service API v1.
- `codex`: local Codex CLI provider.
- `claude`: local Claude CLI provider.
- `claude-huawei`: local Claude CLI provider routed to Huawei/DeepSeek.
- `evaluator`: deterministic local evaluator.

Explicit `edges` support dependency edges, branch conditions, and bounded loops.
The `when` expression is evaluated inside the Temporal workflow from recorded
workflow state only. It can read `input`, `results`, `deps`, `last`, `result`,
`visits`, and `attempts`. This keeps replay deterministic. Loops must be
bounded by `max_visits` and/or top-level `max_total_visits`.

Graph-level `execution_policy` can limit ready-node scheduling. For example,
use `{"mode": "serial", "max_concurrent_nodes": 1}` for rate-limited Agent
runtimes while leaving other task graphs in the default parallel mode.

Dependency artifacts can be passed by reference or inline. Reference mode sends
`artifact_ref`, `uri`, `content_hash`, `mime_type`, `size`, and `summary`;
inline mode embeds content for Agent runtimes that cannot fetch the artifact
API. See `docs/artifact_external_storage.md`.

## Files

- `maos_runtime/` contains the Temporal workflow, graph interpreter, A2A runtime,
  provider registry, runtime config, persistence projection, and HTTP helper code.
- `web/` contains the sandbox HTTP handlers, Agent trace proxy, and browser UI template.
- `simulator/` contains the random-duration simulator microservice and backend helpers.
- `maos_runtime/persistence/` contains the SQLite Execution Store used as the
  primary Web/API read model.
- `services/sandbox_service.py` wires together the Web API/UI, Temporal runtime
  manager, and simulator service for local development. Root `sandbox_service.py`
  is a compatibility entrypoint.
- `services/agent_service/` contains the Agent Service facade used by Multica
  and Hermes backends.
- `scripts/` contains CLI helpers such as `run_dag.py`, `visualize_execution.py`,
  and the simple Temporal greeting example.
- Root `run_dag.py`, `visualize_execution.py`, and `simulator_service.py` are thin
  compatibility entrypoints.
- `examples/order_processing.json` describes the previous order example as data.
- `examples/content_pipeline.json` describes a different dependency graph with another shape.
- `example_cmp/*.json` contains comparison graphs such as single-Agent versus
  multi-Agent coding and research examples.
- `examples/*.json` contains bundled graphs for batch runs, including mixed
  provider, human-in-loop, branch, and loop examples.
- `schemas/task_graph.schema.json` is the formal task graph JSON Schema.
- `docs/` contains the task graph format, API reference, provider lifecycle,
  Agent Service, artifact, human-in-loop, and deployment notes.
- `tests/agent_service/` contains tests for the Multica facade.
- `requirements.txt` pins the Temporal Python SDK and service dependencies.
