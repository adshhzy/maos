"""Temporal workflow for JSON control-flow graphs."""

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from maos_runtime.a2a import extract_result_from_task
from maos_runtime.graph.control_flow import (
    _edge_label,
    _is_condition_node,
    _is_human_node,
    _node_backend_for_display,
    _node_max_visits,
    _node_type,
    _outgoing_edges,
    _predecessor_ids,
    _resolve_value,
    _safe_eval_condition,
    _summarize_result,
    normalize_graph,
)
from maos_runtime.workflows.activities import (
    dispatch_agent_node,
    poll_agent_node,
    resume_agent_node_human_intervention,
)
from maos_runtime.workflows.constants import (
    ACTIVITY_TIMEOUT_SECONDS,
    DEFAULT_MAX_TOTAL_VISITS,
    DEFAULT_NODE_TIMEOUT_SECONDS,
)


DISPATCH_RETRY_POLICY = RetryPolicy(maximum_attempts=5)
WORKFLOW_HISTORY_TEXT_LIMIT = 8000
WORKFLOW_HISTORY_LIST_LIMIT = 20


def _compact_execution_for_history(execution: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "status": execution.get("status"),
        "instance_id": execution.get("instance_id"),
    }
    if execution.get("error") is not None:
        compact["error"] = _compact_value_for_history(execution.get("error"))
    if isinstance(execution.get("result"), dict):
        compact["result"] = _compact_value_for_history(execution["result"])
    if isinstance(execution.get("a2a_task"), dict):
        compact["a2a_task"] = _compact_a2a_task_for_history(execution["a2a_task"])
    return {key: value for key, value in compact.items() if value is not None}


def _compact_dependency_executions_for_activity(
    dependency_executions: dict[str, Any],
) -> dict[str, Any]:
    return {
        str(node_id): _compact_execution_for_history(execution)
        for node_id, execution in dependency_executions.items()
    }


def _compact_a2a_task_for_history(task: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "id": task.get("id"),
        "status": _compact_value_for_history(task.get("status") or {}),
        "metadata": _compact_value_for_history(task.get("metadata") or {}),
    }
    artifacts = task.get("artifacts")
    if isinstance(artifacts, list):
        compact["artifacts"] = [_compact_artifact_for_history(item) for item in artifacts[-WORKFLOW_HISTORY_LIST_LIMIT:]]
    return {key: value for key, value in compact.items() if value not in (None, {}, [])}


def _compact_artifact_for_history(artifact: Any) -> Any:
    if not isinstance(artifact, dict):
        return _compact_value_for_history(artifact)
    return {
        "artifactId": artifact.get("artifactId"),
        "name": artifact.get("name"),
        "description": _compact_value_for_history(artifact.get("description")),
        "metadata": _compact_value_for_history(artifact.get("metadata") or {}),
        "parts": _compact_value_for_history(artifact.get("parts") or []),
    }


def _compact_payload_for_history(payload: Any) -> Any:
    return _compact_value_for_history(payload)


def _result_output_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("latest_comment", "stdout", "result", "output", "response"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    structured = payload.get("structured_output")
    if isinstance(structured, dict):
        for key in ("final_answer", "content", "markdown", "summary"):
            value = structured.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _compact_value_for_history(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= WORKFLOW_HISTORY_TEXT_LIMIT:
            return value
        return value[:WORKFLOW_HISTORY_TEXT_LIMIT] + "...<truncated>"
    if isinstance(value, list):
        return [_compact_value_for_history(item) for item in value[-WORKFLOW_HISTORY_LIST_LIMIT:]]
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        omitted: list[str] = []
        for key, item in value.items():
            key_text = str(key)
            if key_text in {
                "raw",
                "history",
                "full_text",
                "transcript",
                "messages",
                "comments",
                "runs",
                "trace",
                "dependency_results",
                "dependency_artifacts",
            }:
                omitted.append(key_text)
                continue
            compact[key_text] = _compact_value_for_history(item)
        if omitted:
            compact["_omitted_for_workflow_history"] = omitted
        return compact
    return value


@workflow.defn
class JsonDagWorkflow:
    """Temporal workflow for JSON control-flow graphs.

    The historic class name is intentionally kept so existing visibility queries
    and old workflow histories remain readable. New JSON can use explicit
    `edges`, conditional `when` expressions, and bounded loops.
    """

    def __init__(self) -> None:
        self._graph_id = ""
        self._graph_name = ""
        self._graph_type = "control_flow"
        self._workflow_status = "pending"
        self._nodes: dict[str, dict[str, Any]] = {}
        self._levels: list[list[str]] = []
        self._edges: list[dict[str, Any]] = []
        self._agent_events: dict[str, dict[str, Any]] = {}
        self._visits: dict[str, int] = {}
        self._instances: list[dict[str, Any]] = []
        self._latest_completed: dict[str, dict[str, Any]] = {}
        self._all_results: dict[str, Any] = {}
        self._human_responses: dict[str, dict[str, Any]] = {}
        self._human_interventions: dict[str, dict[str, Any]] = {}

    @workflow.run
    async def run(self, graph: dict[str, Any]) -> dict[str, Any]:
        spec = normalize_graph(graph)
        self._initialize_graph(spec)
        self._workflow_status = "running"

        node_specs = {node["id"]: node for node in spec["nodes"]}
        outgoing = _outgoing_edges(spec["edges"])
        predecessor_ids = _predecessor_ids(spec["edges"])
        arrivals: dict[str, dict[str, dict[str, Any]]] = {
            node_id: {} for node_id in node_specs
        }
        running: dict[str, asyncio.Task] = {}
        failure_error: str | None = None
        total_visits = 0
        max_total_visits = int(spec.get("max_total_visits", DEFAULT_MAX_TOTAL_VISITS))

        for node_id in spec["start_nodes"]:
            arrivals[node_id]["__start__"] = self._start_execution()

        while True:
            ready_nodes = [
                node_id
                for node_id in sorted(node_specs)
                if failure_error is None
                and node_id not in running
                and self._node_is_ready(node_specs[node_id], arrivals[node_id], predecessor_ids)
            ]

            if not ready_nodes and not running:
                blocked_by_visit_limit = [
                    node_id
                    for node_id, node in sorted(node_specs.items())
                    if arrivals[node_id]
                    and self._visits.get(node_id, 0) >= _node_max_visits(node)
                ]
                if blocked_by_visit_limit and failure_error is None:
                    node_id = blocked_by_visit_limit[0]
                    failure_error = (
                        f"Node {node_id} received another control-flow arrival but "
                        f"already reached max_visits={_node_max_visits(node_specs[node_id])}"
                    )
                if failure_error is not None:
                    self._workflow_status = "failed"
                    return self._workflow_result(spec, "failed", failure_error)
                self._workflow_status = "completed"
                return self._workflow_result(spec, "completed")

            for node_id in ready_nodes:
                total_visits += 1
                if total_visits > max_total_visits:
                    failure_error = (
                        f"Control-flow graph exceeded max_total_visits={max_total_visits}; "
                        "check loop exit conditions"
                    )
                    break

                visit = self._visits.get(node_id, 0) + 1
                max_visits = _node_max_visits(node_specs[node_id])
                if visit > max_visits:
                    failure_error = (
                        f"Node {node_id} exceeded max_visits={max_visits}; "
                        "check branch conditions"
                    )
                    break

                self._visits[node_id] = visit
                instance_id = f"{node_id}#{visit}"
                node_state = self._nodes[node_id]
                node_state["visits"] = visit
                node_state["current_instance_id"] = instance_id
                node_state["max_visits"] = max_visits
                dependency_executions = self._dependency_executions_for_node(
                    node_specs[node_id],
                    arrivals[node_id],
                )
                arrivals[node_id] = {}
                running[node_id] = asyncio.create_task(
                    self._execute_node(
                        {
                            **node_specs[node_id],
                            "_visit": visit,
                            "_instance_id": instance_id,
                        },
                        dependency_executions,
                        spec.get("input", {}),
                    )
                )

            if failure_error is not None and not running:
                self._workflow_status = "failed"
                return self._workflow_result(spec, "failed", failure_error)

            if not running:
                continue

            done_tasks, _ = await workflow.wait(
                running.values(),
                return_when="FIRST_COMPLETED",
            )

            finished_ids = [
                node_id
                for node_id, task in running.items()
                if task in done_tasks
            ]
            for node_id in sorted(finished_ids):
                try:
                    execution = await running.pop(node_id)
                except Exception as exc:
                    execution = self._failed_execution(node_specs[node_id], str(exc))
                    self._mark_node_failed(node_id, str(exc))

                if execution.get("status") == "failed":
                    failure_error = execution.get("error") or "Agent execution failed"
                    self._workflow_status = "failed"
                    continue

                compact_execution = _compact_execution_for_history(execution)
                self._latest_completed[node_id] = compact_execution
                instance_id = execution.get("instance_id") or f"{node_id}#{self._visits[node_id]}"
                compact_payload = _compact_payload_for_history(execution["result"]["payload"])
                self._all_results[instance_id] = compact_payload
                self._all_results[node_id] = compact_payload

                outgoing_edges = outgoing.get(node_id, [])
                taken_edges = 0
                for edge in outgoing_edges:
                    if self._edge_is_enabled(edge, node_id, execution, spec):
                        to_node = edge["to"]
                        arrivals[to_node][node_id] = compact_execution
                        self._record_edge_taken(edge)
                        taken_edges += 1
                    else:
                        self._record_edge_skipped(edge)
                if (
                    outgoing_edges
                    and taken_edges == 0
                    and all(edge.get("when") for edge in outgoing_edges)
                ):
                    failure_error = (
                        f"Node {node_id} completed but no outgoing branch condition matched"
                    )
                    self._workflow_status = "failed"

            if failure_error is not None and not running:
                return self._workflow_result(spec, "failed", failure_error)

    @workflow.signal
    def agent_node_completed(self, event: dict[str, Any]) -> None:
        node_id = event.get("node_id")
        if not node_id:
            return
        self._agent_events[node_id] = event
        node_state = self._nodes.get(node_id)
        if node_state:
            node_state["a2a_state"] = event.get("a2a_state", "TASK_STATE_COMPLETED")
            node_state["last_heartbeat_at"] = workflow.now().isoformat()
            node_state["heartbeat_count"] += 1
            node_state["summary"] = "agent callback received"
            self._update_latest_instance(node_id, {"summary": "agent callback received"})

    @workflow.signal
    def human_intervention_resolved(self, event: dict[str, Any]) -> None:
        intervention_id = event.get("intervention_id")
        if not intervention_id:
            return
        response = dict(event)
        response.setdefault("status", "resolved")
        response.setdefault("responded_at", workflow.now().isoformat())
        self._human_responses[intervention_id] = response

        intervention = self._human_interventions.get(intervention_id)
        if intervention:
            intervention["status"] = "resolved"
            intervention["responded_at"] = response["responded_at"]
            intervention["responder"] = response.get("responder")
            intervention["response"] = response.get("response", {})
            intervention["decision"] = response.get("decision")
            intervention["comment"] = response.get("comment")
            node_id = intervention.get("node_id")
            node_state = self._nodes.get(node_id)
            if node_state:
                node_state["summary"] = "human response received"
                node_state["last_heartbeat_at"] = workflow.now().isoformat()
                node_state["heartbeat_count"] += 1
                node_state["human_interventions"] = self._node_human_interventions(node_id)
                self._update_latest_instance(node_id, {"summary": "human response received"})

    @workflow.query
    def graph_state(self) -> dict[str, Any]:
        return {
            "graph_id": self._graph_id,
            "graph_name": self._graph_name,
            "graph_type": self._graph_type,
            "workflow_status": self._workflow_status,
            "levels": self._levels,
            "edges": self._edges,
            "nodes": list(self._nodes.values()),
            "instances": list(self._instances),
            "human_interventions": list(self._human_interventions.values()),
            "pending_human_interventions": [
                item
                for item in self._human_interventions.values()
                if item.get("status") == "pending"
            ],
            "results": {
                node_id: execution["result"]["payload"]
                for node_id, execution in self._latest_completed.items()
                if "result" in execution
            },
            "instance_results": dict(self._all_results),
        }

    def _initialize_graph(self, spec: dict[str, Any]) -> None:
        self._graph_id = spec["id"]
        self._graph_name = spec.get("name", spec["id"])
        self._graph_type = spec.get("graph_type", "control_flow")
        self._levels = spec["levels"]
        self._edges = [
            {
                "from": edge["from"],
                "to": edge["to"],
                "label": edge.get("label") or _edge_label(edge),
                "when": edge.get("when"),
                "taken_count": 0,
                "skipped_count": 0,
            }
            for edge in spec["edges"]
        ]
        self._visits = {node["id"]: 0 for node in spec["nodes"]}
        self._nodes = {
            node["id"]: {
                "id": node["id"],
                "label": node.get("label", node["id"]),
                "type": _node_type(node),
                "operation": node.get("operation", "merge"),
                "deps": node.get("deps", []),
                "join": node.get("join", spec.get("default_join", "all")),
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
                "planned_duration_seconds": None,
                "elapsed_seconds": 0,
                "heartbeat_count": 0,
                "last_heartbeat_at": None,
                "simulator_job_id": None,
                "agent_service_task_id": None,
                "hermes_job_id": None,
                "hermes_prompt": None,
                "codex_prompt": None,
                "codex_command": None,
                "codex_workdir": None,
                "claude_task_id": None,
                "claude_prompt": None,
                "claude_command": None,
                "claude_workdir": None,
                "agent_status": None,
                "backend": _node_backend_for_display(node),
                "completion_mode": None,
                "a2a_task_id": None,
                "a2a_state": None,
                "agent_name": None,
                "summary": "",
                "visits": 0,
                "max_visits": _node_max_visits(node),
                "current_instance_id": None,
                "instances": [],
                "human_interventions": [],
            }
            for node in spec["nodes"]
        }

    def _node_is_ready(
        self,
        node_spec: dict[str, Any],
        arrivals: dict[str, dict[str, Any]],
        predecessor_ids: dict[str, set[str]],
    ) -> bool:
        if not arrivals:
            return False
        node_id = node_spec["id"]
        if self._visits.get(node_id, 0) >= _node_max_visits(node_spec):
            return False
        join = str(node_spec.get("join", "all")).lower()
        if join in {"any", "race", "first"}:
            return True
        required = predecessor_ids.get(node_id, set())
        if not required:
            return "__start__" in arrivals
        return required.issubset(set(arrivals))

    def _dependency_executions_for_node(
        self,
        node_spec: dict[str, Any],
        arrivals: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        dependencies = {
            node_id: execution
            for node_id, execution in arrivals.items()
            if node_id != "__start__"
        }
        for dep in node_spec.get("deps", []):
            if dep in self._latest_completed and dep not in dependencies:
                dependencies[dep] = self._latest_completed[dep]
        return dependencies

    async def _execute_node(
        self,
        node_spec: dict[str, Any],
        dependency_executions: dict[str, Any],
        graph_input: dict[str, Any],
    ) -> dict[str, Any]:
        if _is_condition_node(node_spec):
            return await self._execute_condition_node(
                node_spec,
                dependency_executions,
                graph_input,
            )
        if _is_human_node(node_spec):
            return await self._execute_human_node(
                node_spec,
                dependency_executions,
                graph_input,
            )
        return await self._execute_agent_node(
            node_spec,
            dependency_executions,
            graph_input,
        )

    async def _execute_condition_node(
        self,
        node_spec: dict[str, Any],
        dependency_executions: dict[str, Any],
        graph_input: dict[str, Any],
    ) -> dict[str, Any]:
        node_id = node_spec["id"]
        node_state = self._nodes[node_id]
        now = workflow.now().isoformat()
        node_state["status"] = "running"
        node_state["started_at"] = now
        self._record_instance_start(node_spec, "condition", now)

        payload = {
            "node": node_id,
            "operation": node_spec.get("operation", "condition"),
            "status": "completed",
            "decision": _resolve_value(node_spec.get("decision", "evaluated"), graph_input, self._all_results),
            "visit": node_spec.get("_visit", 1),
            "dependency_node_ids": sorted(dependency_executions),
        }
        if isinstance(node_spec.get("params"), dict):
            payload.update(_resolve_value(node_spec["params"], graph_input, self._all_results))

        finished_at = workflow.now().isoformat()
        result = {
            "node": node_id,
            "operation": node_spec.get("operation", "condition"),
            "duration_seconds": 0,
            "payload": payload,
        }
        node_state["status"] = "completed"
        node_state["finished_at"] = finished_at
        node_state["duration_seconds"] = 0
        node_state["elapsed_seconds"] = 0
        node_state["summary"] = f"condition evaluated; visit={node_spec.get('_visit', 1)}"
        self._finish_latest_instance(node_id, "completed", finished_at, node_state["summary"])
        return {
            "status": "completed",
            "result": result,
            "a2a_task": {
                "id": f"control-flow-{node_spec['_instance_id']}",
                "artifacts": [
                    {
                        "name": "dag-node-result",
                        "parts": [{"data": result, "mediaType": "application/json"}],
                    }
                ],
                "metadata": {"backend": "control-flow", "nodeId": node_id},
                "status": {"state": "TASK_STATE_COMPLETED"},
            },
            "instance_id": node_spec["_instance_id"],
        }

    async def _execute_human_node(
        self,
        node_spec: dict[str, Any],
        dependency_executions: dict[str, Any],
        graph_input: dict[str, Any],
    ) -> dict[str, Any]:
        node_id = node_spec["id"]
        node_state = self._nodes[node_id]
        started_at = workflow.now().isoformat()
        intervention_id = f"human-{node_spec['_instance_id']}"
        prompt = (
            node_spec.get("prompt")
            or node_spec.get("description")
            or node_spec.get("label")
            or f"Please review human node {node_id}."
        )
        self._create_human_intervention(
            node_spec=node_spec,
            intervention_id=intervention_id,
            prompt=prompt,
            intervention_type=node_spec.get("human_type", node_spec.get("operation", "approval")),
            schema=node_spec.get("schema") or node_spec.get("response_schema") or {},
            assignee=node_spec.get("assignee"),
            assignee_role=node_spec.get("assignee_role"),
            upstream_node_ids=sorted(dependency_executions),
            created_at=started_at,
        )
        node_state["status"] = "waiting_human"
        node_state["started_at"] = started_at
        node_state["summary"] = f"waiting for human input: {prompt}"
        node_state["human_interventions"] = self._node_human_interventions(node_id)
        self._record_instance_start(node_spec, "human", started_at)
        self._update_latest_instance(
            node_id,
            {
                "status": "waiting_human",
                "summary": node_state["summary"],
                "human_intervention_id": intervention_id,
            },
        )

        response_event = await self._wait_for_human_response(
            intervention_id,
            float(node_spec.get("timeout_seconds", DEFAULT_NODE_TIMEOUT_SECONDS)),
        )
        resolved = self._human_interventions[intervention_id]
        finished_at = workflow.now().isoformat()
        payload = self._human_result_payload(
            intervention_id,
            response_event,
            resolved,
            sorted(dependency_executions),
        )
        result = {
            "node": node_id,
            "operation": node_spec.get("operation", "human"),
            "duration_seconds": (
                workflow.now() - workflow.now()
            ).total_seconds(),
            "payload": payload,
        }
        node_state["status"] = "completed"
        node_state["finished_at"] = finished_at
        node_state["duration_seconds"] = 0
        node_state["elapsed_seconds"] = 0
        node_state["summary"] = _summarize_result(result)
        node_state["human_interventions"] = self._node_human_interventions(node_id)
        self._finish_latest_instance(node_id, "completed", finished_at, node_state["summary"])
        return {
            "status": "completed",
            "result": result,
            "a2a_task": {
                "id": intervention_id,
                "artifacts": [
                    {
                        "name": "dag-node-result",
                        "parts": [{"data": result, "mediaType": "application/json"}],
                    }
                ],
                "metadata": {"backend": "human", "nodeId": node_id},
                "status": {"state": "TASK_STATE_COMPLETED"},
            },
            "instance_id": node_spec["_instance_id"],
        }

    def _create_human_intervention(
        self,
        *,
        node_spec: dict[str, Any],
        intervention_id: str,
        prompt: str,
        intervention_type: str,
        schema: dict[str, Any],
        assignee: Any = None,
        assignee_role: Any = None,
        upstream_node_ids: list[str] | None = None,
        created_at: str | None = None,
        source: str = "graph",
        human_request: dict[str, Any] | None = None,
        a2a_task_id: str | None = None,
    ) -> dict[str, Any]:
        intervention = {
            "id": intervention_id,
            "intervention_id": intervention_id,
            "workflow_id": workflow.info().workflow_id,
            "node_id": node_spec["id"],
            "node_label": node_spec.get("label", node_spec["id"]),
            "instance_id": node_spec["_instance_id"],
            "visit": node_spec.get("_visit", 1),
            "type": intervention_type,
            "source": source,
            "status": "pending",
            "prompt": prompt,
            "schema": schema,
            "assignee": assignee,
            "assignee_role": assignee_role,
            "created_at": created_at or workflow.now().isoformat(),
            "responded_at": None,
            "responder": None,
            "response": None,
            "decision": None,
            "comment": None,
            "upstream_node_ids": upstream_node_ids or [],
            "human_request": human_request or {},
            "human_request_id": (human_request or {}).get("request_id"),
            "a2a_task_id": a2a_task_id,
        }
        self._human_interventions[intervention_id] = intervention
        return intervention

    async def _wait_for_human_response(
        self,
        intervention_id: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        await workflow.wait_condition(
            lambda: intervention_id in self._human_responses,
            timeout=timedelta(seconds=timeout_seconds),
        )
        return self._human_responses.pop(intervention_id)

    def _human_result_payload(
        self,
        intervention_id: str,
        response_event: dict[str, Any],
        resolved: dict[str, Any],
        upstream_node_ids: list[str],
    ) -> dict[str, Any]:
        response_payload = response_event.get("response", {})
        if not isinstance(response_payload, dict):
            response_payload = {"value": response_payload}
        payload = {
            "status": "completed",
            "human_intervention_id": intervention_id,
            "human_intervention_type": resolved.get("type"),
            "decision": response_event.get("decision") or response_payload.get("decision"),
            "comment": response_event.get("comment") or response_payload.get("comment"),
            "responder": response_event.get("responder"),
            "response": response_payload,
            "human_interventions": [resolved],
            "upstream_node_ids": upstream_node_ids,
        }
        payload.update({key: value for key, value in response_payload.items() if key not in payload})
        return payload

    async def _execute_agent_node(
        self,
        node_spec: dict[str, Any],
        dependency_executions: dict[str, Any],
        graph_input: dict[str, Any],
    ) -> dict[str, Any]:
        # Agent 节点只在派发和轮询时短暂占用 worker。真正等待 Agent 工作时，
        # workflow 状态由 Temporal history 持久化，worker 线程可以释放。
        node_state = self._nodes[node_spec["id"]]
        node_state["status"] = "running"
        node_state["started_at"] = workflow.now().isoformat()
        self._record_instance_start(node_spec, "agent", node_state["started_at"])
        a2a_task: dict[str, Any] | None = None

        try:
            send_response = await workflow.execute_activity(
                dispatch_agent_node,
                {
                    "node": node_spec,
                    "dependency_executions": _compact_dependency_executions_for_activity(
                        dependency_executions
                    ),
                    "graph_input": graph_input,
                    "context_id": workflow.info().workflow_id,
                    "workflow_id": workflow.info().workflow_id,
                    "idempotency_key": (
                        f"{workflow.info().workflow_id}:"
                        f"{node_spec['_instance_id']}:dispatch"
                    ),
                },
                start_to_close_timeout=timedelta(seconds=ACTIVITY_TIMEOUT_SECONDS),
                heartbeat_timeout=timedelta(seconds=ACTIVITY_TIMEOUT_SECONDS),
                retry_policy=DISPATCH_RETRY_POLICY,
            )
            a2a_task = send_response["task"]
            a2a_metadata = a2a_task.get("metadata", {})
            node_state["a2a_task_id"] = a2a_task["id"]
            node_state["backend"] = a2a_metadata.get("backend")
            node_state["completion_mode"] = a2a_metadata.get("completionMode")
            node_state["simulator_job_id"] = a2a_metadata.get("simulatorJobId")
            node_state["agent_service_task_id"] = a2a_metadata.get("agentServiceTaskId")
            node_state["hermes_job_id"] = a2a_metadata.get("hermesJobId")
            node_state["hermes_prompt"] = _compact_value_for_history(
                a2a_metadata.get("hermesPrompt")
            )
            node_state["codex_prompt"] = _compact_value_for_history(
                a2a_metadata.get("codexPrompt")
            )
            node_state["codex_command"] = a2a_metadata.get("codexCommand")
            node_state["codex_workdir"] = a2a_metadata.get("codexWorkdir")
            node_state["claude_task_id"] = a2a_metadata.get("claudeTaskId")
            node_state["claude_prompt"] = _compact_value_for_history(
                a2a_metadata.get("claudePrompt")
            )
            node_state["claude_command"] = a2a_metadata.get("claudeCommand")
            node_state["claude_workdir"] = a2a_metadata.get("claudeWorkdir")
            node_state["agent_status"] = a2a_metadata.get("agentStatus")
            node_state["a2a_state"] = a2a_task["status"]["state"]
            node_state["agent_name"] = a2a_metadata.get("agentCard", {}).get("name")
            node_state["planned_duration_seconds"] = a2a_metadata.get(
                "plannedDurationSeconds"
            )
            node_state["status"] = "suspended"
            node_state["summary"] = (
                f"workflow is durably waiting for agent callback from "
                f"{node_state['agent_name']}"
            )
            self._update_latest_instance(
                node_spec["id"],
                {
                    "status": "suspended",
                    "backend": node_state["backend"],
                    "completion_mode": node_state["completion_mode"],
                    "agent_name": node_state["agent_name"],
                    "a2a_task_id": node_state["a2a_task_id"],
                    "agent_service_task_id": node_state["agent_service_task_id"],
                    "simulator_job_id": node_state["simulator_job_id"],
                    "hermes_job_id": node_state["hermes_job_id"],
                    "codex_command": node_state["codex_command"],
                    "codex_workdir": node_state["codex_workdir"],
                    "claude_task_id": node_state["claude_task_id"],
                    "claude_command": node_state["claude_command"],
                    "claude_workdir": node_state["claude_workdir"],
                    "summary": node_state["summary"],
                },
            )
            timeout_seconds = float(
                node_spec.get("timeout_seconds", DEFAULT_NODE_TIMEOUT_SECONDS)
            )
            if a2a_metadata.get("completionMode") == "polling":
                event = await self._poll_until_agent_complete(
                    node_spec,
                    a2a_task,
                    node_state,
                    timeout_seconds,
                )
            else:
                event = await self._wait_until_callback_agent_complete(
                    node_spec,
                    a2a_task,
                    node_state,
                    timeout_seconds,
                )
            if event.get("status") == "failed":
                raise RuntimeError(event.get("error") or "Agent execution failed")

            a2a_task = event["a2a_task"]
            a2a_metadata = a2a_task.get("metadata", {})
            node_state["a2a_state"] = a2a_task["status"]["state"]
            node_state["elapsed_seconds"] = a2a_metadata.get("elapsedSeconds", 0)
            node_state["heartbeat_count"] = a2a_metadata.get(
                "heartbeatCount",
                node_state["heartbeat_count"],
            )
            node_state["last_heartbeat_at"] = a2a_metadata.get(
                "lastHeartbeatAt",
                workflow.now().isoformat(),
            )
            if a2a_task["status"]["state"] == "TASK_STATE_FAILED":
                status_message = a2a_task["status"].get("message", {})
                raise RuntimeError(str(status_message))
            result = extract_result_from_task(a2a_task)
        except Exception as exc:
            error = str(exc)
            self._mark_node_failed(node_spec["id"], error)
            self._workflow_status = "failed"
            return self._failed_execution(node_spec, error, a2a_task)

        node_state["status"] = "completed"
        node_state["finished_at"] = workflow.now().isoformat()
        node_state["duration_seconds"] = result["duration_seconds"]
        node_state["elapsed_seconds"] = result["duration_seconds"]
        node_state["summary"] = _summarize_result(result)
        result_payload = result.get("payload", {})
        output_text = _result_output_text(result_payload)
        artifact_count = len(a2a_task.get("artifacts") or []) if isinstance(a2a_task, dict) else 0
        node_state["output_available"] = bool(output_text)
        node_state["output_chars"] = len(output_text)
        node_state["artifact_count"] = artifact_count
        self._update_latest_instance(
            node_spec["id"],
            {
                "backend": node_state.get("backend"),
                "completion_mode": node_state.get("completion_mode"),
                "agent_name": node_state.get("agent_name"),
                "a2a_task_id": node_state.get("a2a_task_id"),
                "agent_service_task_id": (
                    result_payload.get("agent_service_task_id")
                    or node_state.get("agent_service_task_id")
                ),
                "simulator_job_id": node_state.get("simulator_job_id"),
                "hermes_job_id": (
                    result_payload.get("hermes_job_id")
                    or node_state.get("hermes_job_id")
                ),
                "codex_command": result_payload.get("codex_command")
                or node_state.get("codex_command"),
                "codex_workdir": result_payload.get("codex_workdir")
                or node_state.get("codex_workdir"),
                "claude_task_id": (
                    result_payload.get("claude_task_id")
                    or node_state.get("claude_task_id")
                ),
                "claude_command": result_payload.get("claude_command")
                or node_state.get("claude_command"),
                "claude_workdir": result_payload.get("claude_workdir")
                or node_state.get("claude_workdir"),
                "agent_status": result_payload.get("status") or node_state.get("agent_status"),
                "elapsed_seconds": node_state.get("elapsed_seconds"),
                "heartbeat_count": node_state.get("heartbeat_count"),
                "last_heartbeat_at": node_state.get("last_heartbeat_at"),
                "output_available": node_state.get("output_available"),
                "output_chars": node_state.get("output_chars"),
                "artifact_count": artifact_count,
            },
        )
        self._finish_latest_instance(
            node_spec["id"],
            "completed",
            node_state["finished_at"],
            node_state["summary"],
        )
        return {
            "status": "completed",
            "result": result,
            "a2a_task": a2a_task,
            "instance_id": node_spec["_instance_id"],
        }

    def _edge_is_enabled(
        self,
        edge: dict[str, Any],
        from_node_id: str,
        execution: dict[str, Any],
        spec: dict[str, Any],
    ) -> bool:
        condition = edge.get("when")
        if condition in (None, "", True):
            return True
        if condition is False:
            return False
        context = {
            "input": spec.get("input", {}),
            "results": self._all_results,
            "deps": self._all_results,
            "last": execution["result"]["payload"],
            "result": execution["result"]["payload"],
            "node": {"id": from_node_id, "visit": self._visits.get(from_node_id, 0)},
            "visits": self._visits,
            "attempts": self._visits,
        }
        return bool(_safe_eval_condition(str(condition), context))

    def _workflow_result(
        self,
        spec: dict[str, Any],
        status: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        return {
            "graph_id": spec["id"],
            "graph_name": spec.get("name", spec["id"]),
            "graph_type": spec.get("graph_type", "control_flow"),
            "status": status,
            "node_count": len(spec["nodes"]),
            "instance_count": len(self._instances),
            "error": error,
            "results": {
                node_id: execution["result"]["payload"]
                for node_id, execution in self._latest_completed.items()
                if "result" in execution
            },
            "instance_results": dict(self._all_results),
            "state": self.graph_state(),
        }

    def _failed_execution(
        self,
        node_spec: dict[str, Any],
        error: str,
        a2a_task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        node_state = self._nodes.get(node_spec["id"], {})
        duration_seconds = float(node_state.get("elapsed_seconds") or 0)
        return {
            "status": "failed",
            "error": error,
            "instance_id": node_spec.get("_instance_id"),
            "result": {
                "node": node_spec["id"],
                "operation": node_spec.get("operation", "agent_task"),
                "duration_seconds": duration_seconds,
                "payload": {
                    "status": "failed",
                    "error": error,
                    "agent_backend": node_state.get("backend"),
                    "agent_name": node_state.get("agent_name"),
                    "agent_service_task_id": node_state.get("agent_service_task_id"),
                    "a2a_task_id": node_state.get("a2a_task_id"),
                    "claude_task_id": node_state.get("claude_task_id"),
                    "claude_command": node_state.get("claude_command"),
                    "claude_workdir": node_state.get("claude_workdir"),
                },
            },
            "a2a_task": a2a_task or {"id": node_state.get("a2a_task_id"), "artifacts": []},
        }

    def _mark_node_failed(self, node_id: str, error: str) -> None:
        node_state = self._nodes.get(node_id)
        if not node_state:
            return
        node_state["status"] = "failed"
        node_state["finished_at"] = workflow.now().isoformat()
        node_state["duration_seconds"] = node_state.get("elapsed_seconds") or 0
        node_state["summary"] = error
        self._finish_latest_instance(node_id, "failed", node_state["finished_at"], error)

    async def _wait_until_callback_agent_complete(
        self,
        node_spec: dict[str, Any],
        a2a_task: dict[str, Any],
        node_state: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        deadline = workflow.now() + timedelta(seconds=timeout_seconds)
        while True:
            remaining_seconds = (deadline - workflow.now()).total_seconds()
            if remaining_seconds <= 0:
                raise TimeoutError(
                    f"Agent node {node_spec['id']} did not complete within "
                    f"{timeout_seconds} seconds"
                )
            await workflow.wait_condition(
                lambda: node_spec["id"] in self._agent_events,
                timeout=timedelta(seconds=remaining_seconds),
            )
            event = self._agent_events.pop(node_spec["id"])
            latest_task = event.get("a2a_task") or a2a_task
            a2a_task = latest_task
            self._update_node_from_a2a_task(node_state, latest_task)
            if self._agent_event_requires_human(event):
                a2a_task = await self._handle_agent_human_intervention(
                    node_spec,
                    latest_task,
                    event,
                    node_state,
                    remaining_seconds,
                )
                continue
            return event

    async def _poll_until_agent_complete(
        self,
        node_spec: dict[str, Any],
        a2a_task: dict[str, Any],
        node_state: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        a2a_metadata = a2a_task.get("metadata", {})
        poll_seconds = float(a2a_metadata.get("pollSeconds", 30))
        deadline = workflow.now() + timedelta(seconds=timeout_seconds)

        while True:
            poll_response = await workflow.execute_activity(
                poll_agent_node,
                {
                    "id": a2a_task["id"],
                    "task": a2a_task,
                    "node_id": node_spec["id"],
                    "workflow_id": workflow.info().workflow_id,
                },
                start_to_close_timeout=timedelta(seconds=ACTIVITY_TIMEOUT_SECONDS),
                heartbeat_timeout=timedelta(seconds=ACTIVITY_TIMEOUT_SECONDS),
            )
            latest_task = (
                (poll_response.get("event") or {}).get("a2a_task")
                or poll_response.get("task")
                or a2a_task
            )
            a2a_task = latest_task
            self._update_node_from_a2a_task(node_state, latest_task)

            if self._agent_event_requires_human(
                poll_response.get("event") or {"a2a_task": latest_task}
            ):
                a2a_task = await self._handle_agent_human_intervention(
                    node_spec,
                    latest_task,
                    poll_response.get("event") or {"a2a_task": latest_task},
                    node_state,
                    (deadline - workflow.now()).total_seconds(),
                )
                continue

            if poll_response.get("done"):
                event = poll_response.get("event")
                if event:
                    return event
                return {
                    "workflow_id": workflow.info().workflow_id,
                    "node_id": node_spec["id"],
                    "a2a_task_id": latest_task["id"],
                    "status": (
                        "completed"
                        if latest_task["status"]["state"] == "TASK_STATE_COMPLETED"
                        else "failed"
                    ),
                    "a2a_state": latest_task["status"]["state"],
                    "a2a_task": latest_task,
                }

            remaining_seconds = (deadline - workflow.now()).total_seconds()
            if remaining_seconds <= 0:
                raise TimeoutError(
                    f"Agent node {node_spec['id']} did not complete within "
                    f"{timeout_seconds} seconds"
                )
            await workflow.sleep(timedelta(seconds=min(poll_seconds, remaining_seconds)))

    def _agent_event_requires_human(self, event: dict[str, Any]) -> bool:
        task = event.get("a2a_task") or event.get("task") or {}
        state = ((task.get("status") or {}).get("state") or event.get("a2a_state") or "")
        status = str(event.get("status") or "").lower()
        return state == "TASK_STATE_INPUT_REQUIRED" or status in {
            "needs_input",
            "input_required",
            "waiting_human",
            "human_required",
        }

    async def _handle_agent_human_intervention(
        self,
        node_spec: dict[str, Any],
        a2a_task: dict[str, Any],
        event: dict[str, Any],
        node_state: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        human_request = self._human_request_from_agent_event(event, a2a_task)
        request_id = str(
            human_request.get("request_id")
            or event.get("human_request_id")
            or f"{a2a_task['id']}:human:{len(node_state.get('human_interventions') or []) + 1}"
        )
        human_request["request_id"] = request_id
        intervention_id = self._agent_human_intervention_id(node_spec, request_id)
        if intervention_id not in self._human_interventions:
            self._create_human_intervention(
                node_spec=node_spec,
                intervention_id=intervention_id,
                prompt=(
                    human_request.get("prompt")
                    or human_request.get("description")
                    or f"Agent node {node_spec['id']} is requesting human input."
                ),
                intervention_type=human_request.get("type", "agent_requested_input"),
                schema=human_request.get("schema") or human_request.get("response_schema") or {},
                assignee=human_request.get("assignee"),
                assignee_role=human_request.get("assignee_role"),
                upstream_node_ids=[],
                source="agent",
                human_request=human_request,
                a2a_task_id=a2a_task["id"],
            )
        node_state["status"] = "waiting_human"
        node_state["summary"] = (
            "agent requested human input: "
            f"{self._human_interventions[intervention_id].get('prompt')}"
        )
        node_state["human_interventions"] = self._node_human_interventions(node_spec["id"])
        self._update_latest_instance(
            node_spec["id"],
            {
                "status": "waiting_human",
                "summary": node_state["summary"],
                "human_intervention_id": intervention_id,
            },
        )
        response_event = await self._wait_for_human_response(
            intervention_id,
            max(1.0, timeout_seconds),
        )
        resume_response = await workflow.execute_activity(
            resume_agent_node_human_intervention,
            {
                "id": a2a_task["id"],
                "task": a2a_task,
                "node_id": node_spec["id"],
                "workflow_id": workflow.info().workflow_id,
                "intervention_id": intervention_id,
                "human_request_id": request_id,
                "response": response_event,
                "decision": response_event.get("decision"),
                "comment": response_event.get("comment"),
                "responder": response_event.get("responder"),
            },
            start_to_close_timeout=timedelta(seconds=ACTIVITY_TIMEOUT_SECONDS),
            heartbeat_timeout=timedelta(seconds=ACTIVITY_TIMEOUT_SECONDS),
        )
        resumed_task = resume_response.get("task") or a2a_task
        node_state["status"] = "suspended"
        node_state["summary"] = "human input returned to agent; workflow is durably waiting"
        node_state["human_interventions"] = self._node_human_interventions(node_spec["id"])
        self._update_node_from_a2a_task(node_state, resumed_task)
        self._update_latest_instance(
            node_spec["id"],
            {
                "status": "suspended",
                "summary": node_state["summary"],
                "human_intervention_id": intervention_id,
            },
        )
        return resumed_task

    def _human_request_from_agent_event(
        self,
        event: dict[str, Any],
        a2a_task: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = a2a_task.get("metadata", {})
        status_message = (a2a_task.get("status") or {}).get("message") or {}
        message_payload: dict[str, Any] = {}
        if isinstance(status_message, dict):
            for part in status_message.get("parts", []):
                if isinstance(part, dict) and isinstance(part.get("data"), dict):
                    message_payload = part["data"]
                    break
        human_request = (
            event.get("human_request")
            or metadata.get("humanRequest")
            or message_payload.get("human_request")
            or {}
        )
        return dict(human_request)

    def _agent_human_intervention_id(
        self,
        node_spec: dict[str, Any],
        request_id: str,
    ) -> str:
        safe_request_id = "".join(
            char if char.isalnum() or char in {"-", "_", "#"} else "-"
            for char in request_id
        )
        return f"human-{node_spec['_instance_id']}-agent-{safe_request_id}"

    def _update_node_from_a2a_task(
        self,
        node_state: dict[str, Any],
        a2a_task: dict[str, Any],
    ) -> None:
        a2a_metadata = a2a_task.get("metadata", {})
        node_state["a2a_state"] = a2a_task["status"]["state"]
        node_state["elapsed_seconds"] = a2a_metadata.get(
            "elapsedSeconds",
            node_state["elapsed_seconds"],
        )
        node_state["heartbeat_count"] = a2a_metadata.get(
            "heartbeatCount",
            node_state["heartbeat_count"],
        )
        node_state["last_heartbeat_at"] = a2a_metadata.get(
            "lastHeartbeatAt",
            workflow.now().isoformat(),
        )
        node_state["agent_status"] = a2a_metadata.get(
            "agentStatus",
            node_state.get("agent_status"),
        )
        node_state["summary"] = (
            f"workflow is durably polling {node_state.get('agent_name') or 'agent'}; "
            f"agent status={node_state.get('agent_status') or 'unknown'}"
        )
        self._update_latest_instance(
            node_state["id"],
            {
                "status": node_state["status"],
                "elapsed_seconds": node_state["elapsed_seconds"],
                "heartbeat_count": node_state["heartbeat_count"],
                "last_heartbeat_at": node_state["last_heartbeat_at"],
                "agent_status": node_state["agent_status"],
                "summary": node_state["summary"],
            },
        )

    def _record_instance_start(
        self,
        node_spec: dict[str, Any],
        kind: str,
        started_at: str,
    ) -> None:
        instance = {
            "id": node_spec["_instance_id"],
            "node_id": node_spec["id"],
            "visit": node_spec.get("_visit", 1),
            "kind": kind,
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
            "backend": self._nodes[node_spec["id"]].get("backend"),
            "agent_name": None,
            "a2a_task_id": None,
            "summary": "",
        }
        self._instances.append(instance)
        self._nodes[node_spec["id"]]["instances"] = self._recent_node_instances(node_spec["id"])

    def _update_latest_instance(self, node_id: str, patch: dict[str, Any]) -> None:
        for instance in reversed(self._instances):
            if instance["node_id"] == node_id:
                instance.update(patch)
                break
        if node_id in self._nodes:
            self._nodes[node_id]["instances"] = self._recent_node_instances(node_id)

    def _finish_latest_instance(
        self,
        node_id: str,
        status: str,
        finished_at: str,
        summary: str,
    ) -> None:
        self._update_latest_instance(
            node_id,
            {
                "status": status,
                "finished_at": finished_at,
                "summary": summary,
            },
        )

    def _recent_node_instances(self, node_id: str) -> list[dict[str, Any]]:
        return [
            instance
            for instance in self._instances
            if instance["node_id"] == node_id
        ][-6:]

    def _node_human_interventions(self, node_id: str) -> list[dict[str, Any]]:
        return [
            intervention
            for intervention in self._human_interventions.values()
            if intervention.get("node_id") == node_id
        ][-10:]

    def _record_edge_taken(self, edge: dict[str, Any]) -> None:
        for state_edge in self._edges:
            if state_edge["from"] == edge["from"] and state_edge["to"] == edge["to"]:
                state_edge["taken_count"] += 1
                return

    def _record_edge_skipped(self, edge: dict[str, Any]) -> None:
        for state_edge in self._edges:
            if state_edge["from"] == edge["from"] and state_edge["to"] == edge["to"]:
                state_edge["skipped_count"] += 1
                return

    def _start_execution(self) -> dict[str, Any]:
        return {
            "status": "completed",
            "result": {
                "node": "__start__",
                "operation": "start",
                "duration_seconds": 0,
                "payload": {"status": "completed"},
            },
            "a2a_task": {"id": "__start__", "artifacts": []},
        }



JsonControlFlowWorkflow = JsonDagWorkflow
