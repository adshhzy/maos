from maos_runtime.workflows.agent_events import (
    agent_event_requires_human,
    agent_human_intervention_id,
    human_request_from_agent_event,
    result_output_text,
)
from maos_runtime.workflows.human_interventions import (
    build_human_intervention,
    human_result_payload,
)
from maos_runtime.workflows.state_builders import (
    build_edge_state,
    build_instance_state,
    build_node_state,
    build_start_execution,
)


def test_state_builders_create_visualization_shapes():
    node = {
        "id": "draft",
        "label": "Draft",
        "agent": {"backend": "claude"},
        "max_visits": 3,
    }
    node_state = build_node_state(node)
    instance = build_instance_state(
        node_spec={**node, "_instance_id": "draft#1", "_visit": 1},
        kind="agent",
        started_at="2026-01-01T00:00:00Z",
        backend="claude",
    )
    edge = build_edge_state({"from": "draft", "to": "review", "when": "result.ok"})

    assert node_state["id"] == "draft"
    assert node_state["backend"] == "claude"
    assert node_state["max_visits"] == 3
    assert instance["id"] == "draft#1"
    assert edge["taken_count"] == 0
    assert build_start_execution()["result"]["node"] == "__start__"


def test_human_intervention_helpers_create_stable_payloads():
    node = {"id": "approval", "label": "Approval", "_instance_id": "approval#1", "_visit": 1}
    intervention = build_human_intervention(
        workflow_id="wf-1",
        node_spec=node,
        intervention_id="human-approval#1",
        prompt="Approve?",
        intervention_type="approval",
        schema={"decision": "string"},
        created_at="2026-01-01T00:00:00Z",
        upstream_node_ids=["draft"],
    )
    payload = human_result_payload(
        intervention_id="human-approval#1",
        response_event={
            "responder": "alice",
            "response": {"decision": "approved", "comment": "ok"},
        },
        resolved=intervention,
        upstream_node_ids=["draft"],
    )

    assert intervention["workflow_id"] == "wf-1"
    assert payload["decision"] == "approved"
    assert payload["human_interventions"][0]["id"] == "human-approval#1"


def test_agent_event_helpers_extract_human_requests_and_output():
    task = {
        "status": {
            "state": "TASK_STATE_INPUT_REQUIRED",
            "message": {
                "parts": [
                    {
                        "data": {
                            "human_request": {
                                "request_id": "ask/1",
                                "prompt": "Need a decision",
                            }
                        }
                    }
                ]
            },
        },
        "metadata": {},
    }
    event = {"a2a_task": task}

    assert agent_event_requires_human(event)
    assert human_request_from_agent_event(event, task)["prompt"] == "Need a decision"
    assert agent_human_intervention_id({"_instance_id": "node#1"}, "ask/1") == "human-node#1-agent-ask-1"
    assert result_output_text({"structured_output": {"markdown": "# Done"}}) == "# Done"
