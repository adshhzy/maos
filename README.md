# Temporal Execution Core

This project contains Temporal Python SDK examples. The main example is a
generic JSON control-flow graph interpreter for a persistent multi-Agent
execution sandbox: it reads one or more graphs from JSON, starts one Temporal
workflow per task, schedules runnable Agent nodes by dependency/control edges,
executes ready nodes in parallel, supports conditional branches and bounded
loops, and wakes workflows from either simulator callbacks or durable status
polling of a real Multica-backed Agent Service.

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
Sandbox API + Web UI: http://127.0.0.1:8765
Simulator API:        http://127.0.0.1:8767
Temporal Server:      127.0.0.1:7233
Temporal UI:          http://127.0.0.1:8233
Temporal DB file:     D:\dev\MAOS\temporal-data\temporal.db
```

The sandbox can also connect to the already running Multica Agent Service
facade:

```text
Agent Service API:    http://127.0.0.1:8091
```

The Multica Agent Service facade now lives inside this repository. Start it
from this project when real Multica nodes are needed:

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
details, and refreshes
`/api/tasks` every 30 seconds. Use `Refresh Now` to trigger an immediate
on-demand read from Temporal.

The `All Tasks Board` above the graph summarizes the whole batch:

- Total, running, suspended, completed, and failed workflows.
- Total active activities and aggregate heartbeat count.
- Per-task workflow status and workflow id.
- Per-task node state breakdown.
- Current running or suspended activity nodes.
- Simulator elapsed/planned time and latest heartbeat timestamp.
- Click any board row or tab to switch the graph view to that task.

## Sandbox microservice API

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
`a2a_runtime.py` follows the A2A shape of AgentCard, Message, Part, Task,
TaskStatus, and Artifact:

- Temporal invokes the node's local Agent and gives it an A2A `Message`.
- The A2A message carries upstream dependency data as referenced A2A
  `Artifact` objects from completed parent Agent tasks.
- The Agent immediately returns an A2A `Task` in `TASK_STATE_WORKING`.
- A simulator-backed node posts a callback to the sandbox API when the mock
  work completes.
- A Multica-backed node is created through `POST /tasks` on the Agent Service
  facade. Until that facade exposes push callbacks, the workflow uses durable
  Temporal timers and short polling activities to query `GET /tasks/{id}`.
- Both backends are normalized into completed A2A `Task`/`Artifact` objects.
- The workflow reads the node result from the A2A `Artifact` data part.
- Downstream Agent nodes receive those result artifacts through their own A2A
  input messages.

Temporal activities no longer sleep for the whole node runtime. Instead:

- A short activity invokes the Agent and returns the Agent's A2A task id.
- The workflow sets the node to `suspended`.
- Simulator nodes wait with `workflow.wait_condition(...)` until the callback
  is converted to `agent_node_completed(...)`.
- Multica nodes use `workflow.sleep(...)` plus short `poll_agent_node`
  activities. The sleep is durable and does not hold a worker thread.
- No worker thread is held while the Agent is running.
- Callback or poll fields update `backend`, `agent_service_task_id`,
  `agent_status`, `heartbeat_count`, `last_heartbeat_at`, and
  `elapsed_seconds` for the web UI.

By default simulator jobs run for a random duration between 5 and 120 seconds.
Examples can override this with `simulate.min_seconds` and
`simulate.max_seconds`.

## JSON Control-Flow Format

Top-level fields:

```json
{
  "id": "graph-id",
  "name": "Human readable graph name",
  "graph_type": "control_flow",
  "input": {},
  "start": "first_node",
  "nodes": [],
  "edges": []
}
```

The historic DAG shorthand still works: omit `edges` and put dependency ids in
each node's `deps`. The interpreter converts those dependencies into
unconditional edges and keeps `join=all`, so old examples continue to run.

Each node supports:

```json
{
  "id": "node_id",
  "label": "Human readable node label",
  "type": "agent",
  "operation": "emit",
  "deps": ["upstream_node_id"],
  "join": "all",
  "max_visits": 1,
  "params": {},
  "timeout_seconds": 10,
  "agent": {
    "backend": "simulator"
  },
  "simulate": {
    "min_seconds": 0.5,
    "max_seconds": 4.8
  }
}
```

`type=agent` is the default and means one Agent invocation. `type=condition`
is an internal control node that completes immediately and is usually followed
by conditional outgoing edges. `join=all` waits for every predecessor edge;
`join=any` runs when any incoming edge arrives, which is useful for branches
and loop-back nodes. `max_visits` bounds re-entry for looped nodes.

Explicit edges support branch conditions:

```json
{
  "from": "review_gate",
  "to": "revise_notes",
  "when": "visits.review_gate < 2",
  "label": "needs revision"
}
```

The `when` expression is evaluated inside the Temporal workflow from recorded
workflow state only. It can read `input`, `results`, `deps`, `last`, `result`,
`visits`, and `attempts`. This keeps replay deterministic. Loops must be
bounded by `max_visits` and/or top-level `max_total_visits`.

Any nodes whose join condition is satisfied become runnable together, so
parallelism still comes directly from the graph structure.

Nodes default to the simulator backend. To run a node on the real Multica
Agent Service, set `agent.backend` to `multica` and choose an Agent by
`agent_key`, `agent_id`, or `agent_name`.

Multica nodes default to the requested real Multica Agent. `context_policy`
only controls how much context is sent to the Agent; for example,
`provided_context_only` sends the control-flow node instruction and A2A payload without
duplicating workspace data. To make the handoff explicit, set
`execution_mode=multica` and choose `runtime_profile=codex`.

```json
{
  "id": "architecture_review",
  "label": "Real architecture agent",
  "operation": "agent_task",
  "deps": ["prepare_brief"],
  "agent": {
    "backend": "multica",
    "agent_key": "architect",
    "context_policy": "provided_context_only",
    "runtime_profile": "codex",
    "execution_mode": "multica",
    "status": "in_progress",
    "priority": "high",
    "poll_seconds": 30,
    "prompt": "Produce an architecture integration plan, then add it as a Multica comment and move the task to in_review or done."
  },
  "timeout_seconds": 7200
}
```

If a node should bypass Multica entirely and use the local Hermes one-shot
runtime directly, set `agent.backend` to `hermes`:

```json
{
  "agent": {
    "backend": "hermes",
    "agent_key": "fast_reviewer",
    "context_policy": "provided_context_only",
    "runtime_profile": "hermes_oneshot",
    "execution_mode": "hermes_oneshot",
    "poll_seconds": 30,
    "prompt": "Use only the A2A payload and return the final node result."
  }
}
```

The older compatibility path is still available through AgentService by using
`backend=multica` with `execution_mode=lightweight_hermes_oneshot`. That path
still creates a Multica task and writes the Hermes output back as a Multica
comment, while `backend=hermes` avoids Multica task/comment/status handling.

For token control, the adapter does not duplicate full A2A payloads into
Multica metadata by default. Set `agent.include_payload_metadata` to `true`
only for debugging small payloads. Completed Multica results include recent
comments and run summaries; raw run messages are skipped unless
`AGENT_SERVICE_FETCH_RUN_MESSAGES=true` is set.

Path references inside `params` use `$input.foo` and `$deps.node_id.field`.
Template strings use `${input_foo}` and `${deps_node_id_field}`.

Supported operations:

- `emit`: resolves and returns `params`.
- `merge`: returns resolved params plus dependency payloads.
- `template`: renders `params.fields` using template variables.
- `count`: counts a list from `params.values`.
- `percentage_from_count`: computes `min(cap, count * multiplier)`.
- `status`: returns a status plus optional details.
- `join`: builds an output object from `params.fields`.

The order-processing example is an old-style dependency graph described as JSON in
`examples/order_processing.json`:

```text
load_order -> count_items
load_order -> validate_customer
load_order -> reserve_inventory
count_items -> calculate_discount
validate_customer + calculate_discount -> fraud_check
reserve_inventory + fraud_check -> charge_payment
charge_payment -> arrange_shipping
charge_payment -> send_notification
arrange_shipping + send_notification -> close_order
```

The second example, `examples/content_pipeline.json`, models a content
publishing workflow with parallel copy, SEO, and legal review branches.
`examples/control_flow_review_loop.json` demonstrates explicit control edges,
conditional branches, and a bounded loop that exits through an approval branch.

Most bundled examples are backed by the simulator and run for a random duration
up to 120 seconds by default. `examples/mixed_multica_simulator.json` mixes
real Multica Agent nodes with simulator nodes in one graph. The web UI does not
keep a background task monitor. It reads
Temporal workflow visibility and, only when refreshed, queries each workflow's
`graph_state` or completed result.

## Files

- `maos_runtime/` contains the Temporal workflow, A2A runtime, provider registry,
  task store, runtime config, and HTTP helper code.
- `web/` contains the sandbox HTTP handlers, Agent trace proxy, and browser UI template.
- `simulator/` contains the random-duration simulator microservice and backend helpers.
- `services/sandbox_service.py` wires together the Web API/UI, Temporal runtime manager,
  and simulator service. Root `sandbox_service.py` is a compatibility entrypoint.
- `services/agent_service/` contains the Multica Agent Service facade used by
  real Agent backends.
- `scripts/` contains CLI helpers such as `run_dag.py`, `visualize_execution.py`,
  and the simple Temporal greeting example.
- Root `run_dag.py`, `visualize_execution.py`, and `simulator_service.py` are thin
  compatibility entrypoints.
- `examples/order_processing.json` describes the previous order example as data.
- `examples/content_pipeline.json` describes a different dependency graph with another shape.
- `examples/*.json` contains bundled graphs for batch runs, including a mixed
  Multica + simulator graph.
- `docs/` contains the task graph JSON format, Multica integration API, and Hermes notes.
- `tests/agent_service/` contains tests for the Multica facade.
- `requirements.txt` pins the Temporal Python SDK and service dependencies.
