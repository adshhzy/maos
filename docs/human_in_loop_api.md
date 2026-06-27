# Human-in-the-loop API

This project treats human input as durable workflow state owned by Temporal.
Sandbox API exposes the control-plane endpoints; Web UI and external clients call
these HTTP APIs, and Sandbox API signals the target Temporal workflow.

## Graph node format

Declare a planned human step with `type: "human"`:

```json
{
  "id": "human_release_approval",
  "type": "human",
  "label": "人工发布审批",
  "operation": "approval",
  "deps": ["draft_release_plan"],
  "prompt": "请审批是否允许进入灰度发布。",
  "assignee_role": "release_owner",
  "schema": {
    "decision": ["approved", "needs_revision", "rejected"],
    "comment": "审批意见"
  },
  "timeout_seconds": 86400
}
```

When the workflow reaches this node, it creates a human intervention, marks the
node as `waiting_human`, and durably waits for a Temporal signal. Worker threads
are not occupied while the workflow is waiting.

## List human interventions

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
      "prompt": "请审批是否允许进入灰度发布。",
      "schema": {"decision": ["approved", "needs_revision", "rejected"]}
    }
  ]
}
```

## Submit a human response

```http
POST /api/tasks/{workflow_id}/human-interventions/{intervention_id}/responses
Content-Type: application/json
```

```json
{
  "responder": "operator",
  "decision": "approved",
  "comment": "同意进入灰度发布，请保留人工兜底和回滚预案。",
  "response": {
    "decision": "approved",
    "comment": "同意进入灰度发布，请保留人工兜底和回滚预案。"
  }
}
```

Sandbox API sends `JsonDagWorkflow.human_intervention_resolved` to Temporal.
The human node completes and produces a standard `dag-node-result` A2A artifact,
so downstream Agent or Simulator nodes receive the human decision through
`$deps.<human_node_id>`.

## Runtime state fields

Task state includes:

- `state.human_interventions`: all human interventions in the workflow.
- `state.pending_human_interventions`: unresolved interventions only.
- `node.status == "waiting_human"`: node is durably waiting for a human signal.
- `node.human_interventions`: interventions attached to that node.

Node result payload includes:

```json
{
  "status": "completed",
  "decision": "approved",
  "comment": "...",
  "responder": "operator",
  "response": {"decision": "approved", "comment": "..."},
  "human_interventions": [...]
}
```

## Example

See `examples/human_in_loop_release_approval.json`.

## Agent runtime requests human input

An Agent runtime can request human input while an Agent node is still running.
The workflow uses the same intervention store and the same
`JsonDagWorkflow.human_intervention_resolved` signal as planned `type: "human"`
nodes.

Agent Service or Simulator reports the intermediate state through the existing
callback endpoint:

```http
POST /api/agent-callbacks
Content-Type: application/json
```

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

The Web UI or any external client resolves it with the same human response API:

```http
POST /api/tasks/{workflow_id}/human-interventions/{intervention_id}/responses
```

After the signal is received, the workflow resumes the original Agent task
through the provider API:

```http
POST /tasks/{agent_task_id}/human-responses
```

Simulator implements the equivalent local endpoint:

```http
POST /api/simulator/jobs/{job_id}/human-responses
```

An Agent node may request human input multiple times. Each request gets a stable
intervention id derived from the node instance and `human_request.request_id`,
and all resolved responses are preserved in `node.human_interventions`.

Simulation example:

```text
examples/agent_runtime_human_intervention_simulator.json
```
