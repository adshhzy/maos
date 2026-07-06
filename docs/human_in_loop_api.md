# Human-in-the-loop API

MAOS treats human input as durable workflow state owned by Temporal. The Sandbox
API exposes HTTP control-plane endpoints for Web UI and external clients. The
Sandbox API then signals the target Temporal workflow.

Human input can enter the system in two ways:

- A planned graph node with `type: "human"`.
- A running Agent task reports `needs_input` / `input_required`.

Both paths use the same intervention data model and the same response endpoint.

## Planned Human Node

Declare a planned human step with `type: "human"`:

```json
{
  "id": "human_release_approval",
  "type": "human",
  "label": "Manual release approval",
  "operation": "approval",
  "deps": ["draft_release_plan"],
  "prompt": "Please decide whether this release may proceed.",
  "assignee_role": "release_owner",
  "schema": {
    "decision": ["approved", "needs_revision", "rejected"],
    "comment": "Approval comment"
  },
  "timeout_seconds": 86400
}
```

When the workflow reaches this node, it creates a human intervention, marks the
node as `waiting_human`, and durably waits for a Temporal signal. No worker
thread is occupied while the workflow is waiting.

## List Human Interventions

```http
GET /api/human-interventions?status=pending
GET /api/human-interventions?task_id={workflow_id}
GET /api/tasks/{workflow_id}/human-interventions?status=pending
```

Response:

```json
{
  "human_interventions": [
    {
      "intervention_id": "human-human_release_approval#1",
      "workflow_id": "task-human-in-loop-release-approval-...",
      "node_id": "human_release_approval",
      "status": "pending",
      "prompt": "Please decide whether this release may proceed.",
      "schema": {
        "decision": ["approved", "needs_revision", "rejected"]
      }
    }
  ]
}
```

## Submit Human Response

```http
POST /api/tasks/{workflow_id}/human-interventions/{intervention_id}/responses
Content-Type: application/json
```

```json
{
  "responder": "operator",
  "decision": "approved",
  "comment": "Approved with rollback guardrail.",
  "response": {
    "decision": "approved",
    "comment": "Approved with rollback guardrail."
  }
}
```

Sandbox API sends `JsonDagWorkflow.human_intervention_resolved` to Temporal.
The human node completes and produces a standard `dag-node-result` A2A artifact,
so downstream Agent or Simulator nodes receive the human decision through normal
dependency transfer.

## Runtime State Fields

Task state includes:

- `state.human_interventions`: all interventions in the workflow.
- `state.pending_human_interventions`: unresolved interventions only.
- `node.status == "waiting_human"`: node is waiting for a human signal.
- `node.human_interventions`: interventions attached to that node.

Completed human node result payload:

```json
{
  "status": "completed",
  "decision": "approved",
  "comment": "Approved with rollback guardrail.",
  "responder": "operator",
  "response": {
    "decision": "approved",
    "comment": "Approved with rollback guardrail."
  },
  "human_interventions": []
}
```

## Agent Runtime Requests Human Input

An Agent runtime can request human input while an Agent node is still running.
The workflow uses the same intervention store and the same
`JsonDagWorkflow.human_intervention_resolved` Temporal signal as planned human
nodes.

Agent Service or Simulator reports the intermediate state through:

```http
POST /api/agent-callbacks
POST /api/v1/agent-events
Content-Type: application/json
```

Example callback:

```json
{
  "workflow_id": "task-...",
  "node_id": "draft_contract_terms_agent",
  "a2a_task_id": "a2a-task-...",
  "status": "needs_input",
  "human_request": {
    "request_id": "payment-confirmation",
    "prompt": "Please confirm whether advance payment is allowed.",
    "assignee_role": "business_owner",
    "schema": {
      "decision": ["allow_advance_payment", "no_advance_payment"],
      "comment": "Payment guidance"
    }
  }
}
```

The workflow converts this into a pending human intervention with:

- `source: "agent"`
- `type: "agent_requested_input"`
- `human_request_id`
- `a2a_task_id`

The Web UI or any external client resolves it with:

```http
POST /api/tasks/{workflow_id}/human-interventions/{intervention_id}/responses
```

After the signal is received, the workflow resumes the original Agent task
through the provider API. For Agent Service backed tasks, the stable HTTP
endpoint is:

```http
POST /api/v1/agent-tasks/{agent_task_id}/resume
```

Simulator-backed tasks use the simulator's local equivalent.

An Agent node may request human input multiple times. Each request gets a stable
intervention id derived from the node instance and `human_request.request_id`,
and all resolved responses are preserved in `node.human_interventions`.

## Examples

- `examples/human_in_loop_release_approval.json`
- `examples/agent_runtime_human_intervention_simulator.json`

See `docs/api_reference.md` for the full API map.
