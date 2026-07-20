# MAOS Task Graph JSON Schema

This document describes the JSON task graph format used by the persistent
execution layer. The formal schema lives at:

```text
schemas/task_graph.schema.json
```

The Python validator is:

```text
maos_runtime.graph.schema.validate_task_graph
```

The JSON Schema is intentionally extension-friendly. Provider-specific fields
may be added under `agent`, `params`, or top-level metadata, while the Python
semantic validator enforces stable graph references, branch expression syntax,
and DAG/control-flow constraints.

Command-line validation:

```powershell
.\.venv\Scripts\python.exe -m maos_runtime.graph.validate examples
.\.venv\Scripts\python.exe -m maos_runtime.graph.validate examples\content_pipeline.json
```

## Top-Level Fields

Required:

- `id`: stable graph id. Use letters, numbers, `_`, `-`, or `.`.
- `nodes`: non-empty list of node objects.

Optional:

- `name`: display name.
- `graph_type`: `dag` or `control_flow`.
- `type`: alias for `graph_type`.
- `input`: graph-level input object.
- `start`: one start node id or a list of start node ids.
- `start_nodes`: list form of `start`.
- `default_join`: `all`, `any`, `race`, or `first`.
- `max_total_visits`: maximum total node executions across loops.
- `execution_policy`: optional graph-level scheduling policy. Use
  `{"mode": "serial", "max_concurrent_nodes": 1}` to run ready nodes one at a
  time; omit it to keep the default parallel scheduling. Compatible mode names
  are `serial`, `sequential`, `single`, and `one_at_a_time`.
- `executionPolicy`: compatibility alias for `execution_policy`.
- `edges`: explicit control-flow edges.

If `edges` is omitted, dependencies are inferred from `node.deps` and the graph
must be acyclic. If `edges` is present, loops are allowed when
`graph_type: "control_flow"` and should be bounded with `max_visits` and
`max_total_visits`.

## Node Fields

Required:

- `id`: stable node id.

Common optional fields:

- `label`: display label.
- `type`: `agent`, `condition`, `decision`, `router`, `branch`, `human`,
  `human_node`, `human_intervention`, or `approval`.
- `operation`: simulator/control operation, such as `emit`, `merge`, `join`,
  `status`, `template`, or `agent_task`.
- `deps`: dependency node ids.
- `join`: `all`, `any`, `race`, or `first`.
- `max_visits`: maximum times this node may execute in a control-flow loop.
- `timeout_seconds`: node timeout.
- `result_text_limit`: maximum characters preserved in this node's result
  artifact for downstream nodes. This is useful for expert Agent nodes whose
  full output must be consumed by later synthesis nodes.
- `params`: operation parameters.

Agent node options:

```json
{
  "id": "security_review",
  "operation": "agent_task",
  "deps": ["draft_plan"],
  "agent": {
    "backend": "multica",
    "agent_key": "security_reviewer",
    "context_policy": "provided_context_only",
    "runtime_profile": "maos_compact_agent",
    "execution_mode": "multica",
    "poll_seconds": 30,
    "timeout_seconds": 900,
    "result_text_limit": 9000,
    "prompt_payload_limit": 65000,
    "prompt": "Review the upstream release plan."
  }
}
```

Built-in `agent.backend` values:

| Backend | Description |
| --- | --- |
| `simulator` | Local simulator provider for tests, demos, random runtime, and simulated human intervention |
| `multica` | External Multica Agent through Agent Service API v1 |
| `hermes` | Hermes lightweight/one-shot mode through Agent Service API v1 |
| `codex` | Local Codex CLI provider |
| `claude` | Local Claude CLI provider |
| `claude-huawei` | Local Claude CLI routed to Huawei/DeepSeek |
| `evaluator` | Deterministic local evaluator provider |

Common aliases are normalized by the provider registry, such as
`direct-hermes`, `codex-cli`, `claude-cli`, `deepseek-v3.2`, and
`deterministic-evaluator`.

Artifact transfer options:

```json
{
  "agent": {
    "backend": "claude",
    "artifact_transfer_mode": "ref"
  }
}
```

Use `ref` to pass only `artifact_ref`, `uri`, `content_hash`, `mime_type`,
`size`, and `summary` to downstream nodes. Use `inline` when an Agent runtime
cannot access the artifact API and must receive upstream business content
directly. Inline mode removes runtime-only noise fields but does not truncate
long business text strings. `dependency_artifact_mode` is a compatibility alias.

Simulator options:

```json
{
  "id": "simulate_work",
  "backend": "simulator",
  "operation": "merge",
  "simulate": {
    "min_seconds": 2,
    "max_seconds": 5
  }
}
```

Human node options:

```json
{
  "id": "release_approval",
  "type": "human",
  "operation": "approval",
  "deps": ["draft_release_plan"],
  "prompt": "Approve the release?",
  "assignee_role": "release_owner",
  "schema": {
    "decision": ["approved", "needs_revision", "rejected"],
    "comment": "Approval comment"
  },
  "timeout_seconds": 86400
}
```

Runtime human intervention simulation inside a simulator-backed Agent node:

```json
{
  "id": "draft_contract_terms_agent",
  "backend": "simulator",
  "operation": "merge",
  "simulate": {
    "min_seconds": 8,
    "max_seconds": 12,
    "human_interventions": [
      {
        "request_id": "payment-confirmation",
        "after_seconds": 2,
        "prompt": "Confirm whether advance payment is allowed.",
        "assignee_role": "business_owner",
        "schema": {
          "decision": ["allow_advance_payment", "no_advance_payment"],
          "comment": "Payment guidance"
        }
      }
    ]
  }
}
```

## Edge Fields

Required:

- `from`: source node id.
- `to`: target node id.

Optional:

- `label`: display label.
- `when`: branch expression string, boolean, or null.
- `kind`: `control`, `dependency`, `branch`, `loop`, or `fallback`.

Branch example:

```json
{
  "from": "review_gate",
  "to": "publish",
  "label": "approved",
  "when": "result.decision == 'approved'",
  "kind": "branch"
}
```

Loop example:

```json
{
  "from": "review_gate",
  "to": "revise_plan",
  "label": "needs revision",
  "when": "result.decision == 'needs_revision'",
  "kind": "loop"
}
```

Allowed condition variables include:

- `input`: graph input.
- `results` / `deps`: completed node results.
- `last` / `result`: result payload from the edge source node.
- `node`: source node metadata.
- `visits` / `attempts`: visit counters.

Supported functions in runtime evaluation:

- `len`
- `int`
- `float`
- `str`
- `bool`
- `min`
- `max`

## Validation Rules

The validator performs both JSON Schema and semantic checks:

- graph is an object with `id` and non-empty `nodes`.
- node ids and graph id use a stable id pattern.
- node ids are unique.
- `deps`, `start`, and `edges` reference existing nodes.
- duplicate edges with identical `from`, `to`, and `when` are rejected.
- `simulate.min_seconds <= simulate.max_seconds`.
- branch expressions in `edge.when` must be valid Python expression syntax.
- inferred DAGs without explicit `edges` must be acyclic.
- explicit `edges` with `graph_type: "dag"` must be acyclic.
- graph-level `execution_policy` is normalized to either parallel scheduling or
  a positive max-concurrent-node limit.

Control-flow graphs may contain loops, but must use bounded visit counts:

```json
{
  "graph_type": "control_flow",
  "max_total_visits": 20,
  "nodes": [
    {"id": "draft", "max_visits": 3},
    {"id": "review_gate", "type": "condition", "max_visits": 3}
  ]
}
```

## Python Usage

```python
import json
from maos_runtime.graph.schema import GraphValidationError, validate_task_graph

graph = json.loads(open("examples/content_pipeline.json", encoding="utf-8").read())

try:
    validate_task_graph(graph)
except GraphValidationError as exc:
    for issue in exc.issues:
        print(issue.format())
```

## Compatibility Notes

The schema allows additional properties so existing examples and future provider
extensions remain compatible. Stable cross-field behavior is enforced by the
Python semantic validator rather than only by JSON Schema.

For a fuller authoring guide with Chinese examples, see
`docs/task_graph_json_format.md`.
