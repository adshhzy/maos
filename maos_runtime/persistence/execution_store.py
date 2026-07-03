"""Unified SQLite projection for durable workflow execution state.

This store is intentionally a projection over the existing Temporal workflow
state, provider task store, task archive, and artifact files. It gives Web/API
readers a single stable model while the Temporal workflow remains the execution
engine.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
_STORE_LOCK = threading.RLock()


def execution_store_db_file() -> str:
    configured = os.environ.get("MAOS_EXECUTION_DB_FILE")
    if configured:
        return str(Path(configured))
    base = Path(os.environ.get("MAOS_DATA_DIR", r"D:\dev\MAOS\temporal-data"))
    return str(base / "execution_store.sqlite3")


def persist_workflow_task_snapshot(
    task: dict[str, Any],
    *,
    source: str = "workflow_snapshot",
) -> None:
    """Persist a full workflow/task snapshot into the unified execution model."""

    workflow_id = str(task.get("workflow_id") or task.get("task_id") or "")
    if not workflow_id:
        return
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    state = task.get("state") if isinstance(task.get("state"), dict) else {}
    if not state and isinstance(result.get("state"), dict):
        state = result["state"]
    status = str(task.get("status") or result.get("status") or state.get("workflow_status") or "unknown")
    now = _timestamp()
    graph_id = str(task.get("graph_id") or result.get("graph_id") or state.get("graph_id") or workflow_id)
    graph_name = str(task.get("graph_name") or result.get("graph_name") or state.get("graph_name") or graph_id)
    graph_type = task.get("graph_type") or result.get("graph_type") or state.get("graph_type")
    submitted_at = _time_value(task.get("submitted_at"))
    updated_at = _time_value(task.get("updated_at") or task.get("archived_at") or now)
    finished_at = _finished_at_from_task(task, status)

    with _STORE_LOCK, _connection() as conn:
        _ensure_schema(conn)
        _upsert_graph_definition(conn, graph_id, graph_name, graph_type, state)
        conn.execute(
            """
            INSERT INTO workflow_runs (
                workflow_id, graph_id, graph_name, graph_version, status,
                submitted_at, started_at, finished_at, updated_at,
                temporal_workflow_id, error, snapshot_json, source, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workflow_id) DO UPDATE SET
                graph_id = excluded.graph_id,
                graph_name = excluded.graph_name,
                graph_version = excluded.graph_version,
                status = excluded.status,
                submitted_at = COALESCE(excluded.submitted_at, workflow_runs.submitted_at),
                finished_at = COALESCE(excluded.finished_at, workflow_runs.finished_at),
                updated_at = excluded.updated_at,
                temporal_workflow_id = excluded.temporal_workflow_id,
                error = excluded.error,
                snapshot_json = excluded.snapshot_json,
                source = excluded.source
            """,
            (
                workflow_id,
                graph_id,
                graph_name,
                _graph_version(state),
                status,
                submitted_at,
                _time_value(task.get("started_at")),
                finished_at,
                updated_at,
                str(task.get("workflow_id") or workflow_id),
                _json_or_text(task.get("error") or result.get("error")),
                _json_dumps(task),
                source,
                now,
            ),
        )
        _persist_nodes(conn, workflow_id, state, result)
        _persist_edges(conn, workflow_id, state)
        _persist_human_interventions(conn, workflow_id, state)
        _persist_evaluation_reports(conn, workflow_id, state, result)
        _insert_event(
            conn,
            workflow_id=workflow_id,
            event_type=f"workflow.{status}",
            payload={"source": source, "status": status},
            event_time=updated_at or now,
        )
        conn.commit()


def persist_provider_task_record(record: dict[str, Any]) -> None:
    """Project a provider task record into provider_invocations/artifacts."""

    task = record.get("task") if isinstance(record.get("task"), dict) else None
    if not isinstance(task, dict):
        return
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    provider_task_id = str(record.get("a2a_task_id") or task.get("id") or "")
    if not provider_task_id:
        return
    workflow_id = str(record.get("workflow_id") or metadata.get("workflow_id") or metadata.get("workflowId") or "")
    node_id = str(record.get("node_id") or metadata.get("nodeId") or "")
    backend = str(record.get("backend") or metadata.get("backend") or "")
    status = str(record.get("status") or (task.get("status") or {}).get("state") or "")
    created_at = _time_value(record.get("created_at") or metadata.get("createdAt"))
    updated_at = _time_value(record.get("updated_at") or _timestamp())
    finished_at = _time_value(record.get("finished_at") or metadata.get("finishedAt"))

    with _STORE_LOCK, _connection() as conn:
        _ensure_schema(conn)
        if workflow_id:
            _upsert_workflow_stub(conn, workflow_id, updated_at)
        conn.execute(
            """
            INSERT INTO provider_invocations (
                provider_invocation_id, workflow_id, node_instance_id, node_id,
                provider, external_task_id, idempotency_key, status,
                created_at, submitted_at, started_at, finished_at, updated_at,
                last_heartbeat_at, output_artifact_id, trace_artifact_id,
                error, task_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_invocation_id) DO UPDATE SET
                workflow_id = COALESCE(excluded.workflow_id, provider_invocations.workflow_id),
                node_instance_id = COALESCE(excluded.node_instance_id, provider_invocations.node_instance_id),
                node_id = COALESCE(excluded.node_id, provider_invocations.node_id),
                provider = COALESCE(excluded.provider, provider_invocations.provider),
                external_task_id = excluded.external_task_id,
                idempotency_key = COALESCE(excluded.idempotency_key, provider_invocations.idempotency_key),
                status = excluded.status,
                finished_at = COALESCE(excluded.finished_at, provider_invocations.finished_at),
                updated_at = excluded.updated_at,
                last_heartbeat_at = COALESCE(excluded.last_heartbeat_at, provider_invocations.last_heartbeat_at),
                output_artifact_id = COALESCE(excluded.output_artifact_id, provider_invocations.output_artifact_id),
                trace_artifact_id = COALESCE(excluded.trace_artifact_id, provider_invocations.trace_artifact_id),
                error = COALESCE(excluded.error, provider_invocations.error),
                task_json = excluded.task_json
            """,
            (
                provider_task_id,
                workflow_id or None,
                str(metadata.get("instance_id") or metadata.get("instanceId") or "") or None,
                node_id or None,
                backend or None,
                provider_task_id,
                record.get("idempotency_key") or metadata.get("idempotencyKey"),
                status or None,
                created_at,
                created_at,
                created_at,
                finished_at,
                updated_at,
                metadata.get("lastHeartbeatAt"),
                _first_artifact_id(task),
                None,
                _status_message(task),
                _json_dumps(task),
            ),
        )
        for artifact in task.get("artifacts", []) if isinstance(task.get("artifacts"), list) else []:
            if isinstance(artifact, dict):
                _persist_a2a_artifact(
                    conn,
                    artifact,
                    workflow_id=workflow_id or None,
                    node_instance_id=str(metadata.get("instance_id") or metadata.get("instanceId") or "") or None,
                    provider_invocation_id=provider_task_id,
                )
        _insert_event(
            conn,
            workflow_id=workflow_id or provider_task_id,
            node_instance_id=str(metadata.get("instance_id") or metadata.get("instanceId") or "") or None,
            provider_invocation_id=provider_task_id,
            event_type="provider.updated",
            payload={"provider": backend, "status": status, "provider_task_id": provider_task_id},
            event_time=updated_at or _timestamp(),
        )
        conn.commit()


def persist_artifact_record(record: dict[str, Any]) -> None:
    """Persist metadata for an artifact-store record."""

    artifact_id = str(record.get("artifact_ref") or record.get("artifactId") or "")
    if not artifact_id:
        return
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    workflow_id = (
        metadata.get("workflow_id")
        or metadata.get("workflowId")
        or metadata.get("producer_workflow_id")
        or metadata.get("consumer_workflow_id")
    )
    node_instance_id = metadata.get("instance_id") or metadata.get("instanceId")
    node_id = metadata.get("nodeId") or metadata.get("node_id")
    with _STORE_LOCK, _connection() as conn:
        _ensure_schema(conn)
        _upsert_artifact_metadata(
            conn,
            artifact_id=artifact_id,
            workflow_id=str(workflow_id) if workflow_id else None,
            node_instance_id=str(node_instance_id) if node_instance_id else None,
            provider_invocation_id=str(metadata.get("a2aTaskId") or metadata.get("provider_invocation_id") or "") or None,
            kind=str(metadata.get("kind") or record.get("name") or "artifact"),
            uri=record.get("internal_uri") or record.get("uri"),
            mime_type=record.get("mime_type"),
            size_bytes=record.get("size"),
            sha256=record.get("content_hash") or record.get("sha256"),
            summary=record.get("summary"),
            preview=record.get("preview"),
            metadata={
                **metadata,
                **({"node_id": node_id} if node_id else {}),
                "source_artifact_id": record.get("source_artifact_id"),
            },
        )
        conn.commit()


def load_workflow_task_snapshot(workflow_id: str) -> dict[str, Any] | None:
    with _STORE_LOCK, _connection() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT snapshot_json FROM workflow_runs WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
    if not row or not row["snapshot_json"]:
        return None
    try:
        return json.loads(row["snapshot_json"])
    except Exception:
        return None


def load_evaluation_report(evaluation_id: str) -> dict[str, Any] | None:
    """Load a structured evaluator report by evaluator/provider task id."""

    with _STORE_LOCK, _connection() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM evaluation_reports WHERE evaluation_id = ?",
            (evaluation_id,),
        ).fetchone()
        if row is None:
            return None
        case_rows = conn.execute(
            """
            SELECT candidate, test_name, display_name, status, duration_seconds,
                   failure_artifact_id, payload_json
            FROM evaluation_test_cases
            WHERE evaluation_id = ?
            ORDER BY rowid
            """,
            (evaluation_id,),
        ).fetchall()

    try:
        report = json.loads(row["report_json"] or "{}")
    except Exception:
        report = {}
    if not isinstance(report, dict):
        report = {}

    report.setdefault("evaluation_task_id", evaluation_id)
    report.setdefault("benchmark_id", row["benchmark_id"])
    report.setdefault("winner", row["winner"])
    report.setdefault("score_delta", row["score_delta"])
    for side in ("single", "multi"):
        report.setdefault(side, {})
        if isinstance(report[side], dict):
            score = row[f"{side}_score"]
            if score is not None:
                report[side].setdefault("score", score)
    report["execution_store_source"] = True
    report["source_workflow_id"] = row["workflow_id"]

    cases_by_candidate: dict[str, list[dict[str, Any]]] = {"single": [], "multi": []}
    for case_row in case_rows:
        candidate = str(case_row["candidate"] or "")
        if candidate not in cases_by_candidate:
            continue
        payload = _json_loads_dict(case_row["payload_json"])
        item = {
            **payload,
            "name": payload.get("name") or case_row["test_name"],
            "display": payload.get("display") or case_row["display_name"] or case_row["test_name"],
            "status": payload.get("status") or case_row["status"] or "unknown",
        }
        if case_row["duration_seconds"] is not None:
            item.setdefault("duration_seconds", case_row["duration_seconds"])
        if case_row["failure_artifact_id"]:
            item.setdefault("failure_artifact_id", case_row["failure_artifact_id"])
        cases_by_candidate[candidate].append(item)
    for candidate, cases in cases_by_candidate.items():
        if cases and isinstance(report.get(candidate), dict):
            report[candidate]["hidden_test_cases"] = cases
    if not isinstance(report.get("test_matrix"), dict):
        matrix = _evaluation_test_matrix(report)
        if matrix.get("tests"):
            report["test_matrix"] = matrix
    return report


def list_workflow_runs(limit: int | None = 100) -> list[dict[str, Any]]:
    sql = (
        "SELECT workflow_id, graph_id, graph_name, graph_version, status, "
        "submitted_at, started_at, finished_at, updated_at, source "
        "FROM workflow_runs ORDER BY COALESCE(updated_at, created_at) DESC"
    )
    params: tuple[Any, ...] = ()
    if limit:
        sql += " LIMIT ?"
        params = (int(limit),)
    with _STORE_LOCK, _connection() as conn:
        _ensure_schema(conn)
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def _evaluation_test_matrix(report: dict[str, Any]) -> dict[str, Any]:
    single_cases = _evaluation_case_map(report.get("single"))
    multi_cases = _evaluation_case_map(report.get("multi"))
    names: list[str] = []
    for cases in (single_cases, multi_cases):
        for name in cases:
            if name not in names:
                names.append(name)
    return {
        "tests": [
            {
                "name": name,
                "display": (
                    single_cases.get(name, {}).get("display")
                    or multi_cases.get(name, {}).get("display")
                    or name
                ),
            }
            for name in names
        ],
        "rows": [
            {
                "candidate": "single",
                "label": "Single Agent",
                "statuses": {
                    name: single_cases.get(name, {}).get("status", "unknown")
                    for name in names
                },
            },
            {
                "candidate": "multi",
                "label": "Multi Agent",
                "statuses": {
                    name: multi_cases.get(name, {}).get("status", "unknown")
                    for name in names
                },
            },
        ],
    }


def _evaluation_case_map(candidate: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(candidate, dict) or not isinstance(candidate.get("hidden_test_cases"), list):
        return {}
    mapped: dict[str, dict[str, Any]] = {}
    for item in candidate["hidden_test_cases"]:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        mapped[str(item["name"])] = item
    return mapped


def _persist_nodes(
    conn: sqlite3.Connection,
    workflow_id: str,
    state: dict[str, Any],
    result: dict[str, Any],
) -> None:
    nodes = [node for node in state.get("nodes", []) if isinstance(node, dict)]
    by_node = {str(node.get("id")): node for node in nodes if node.get("id")}
    instances = [
        instance
        for instance in state.get("instances", [])
        if isinstance(instance, dict) and instance.get("id")
    ]
    if not instances:
        instances = [_instance_from_node(node) for node in nodes]
    instance_results = _instance_results(state, result)
    for instance in instances:
        node_id = str(instance.get("node_id") or instance.get("nodeId") or "")
        node = by_node.get(node_id, {})
        node_instance_id = str(instance.get("id") or f"{node_id}#1")
        output_artifact_id = _first_artifact_id({"artifacts": instance.get("artifacts") or []})
        provider_invocation_id = _provider_invocation_id_from_instance(instance, node)
        payload = instance_results.get(node_instance_id) or instance_results.get(node_id) or {}
        conn.execute(
            """
            INSERT INTO node_instances (
                node_instance_id, workflow_id, node_id, visit, attempt,
                node_type, backend, status, decision, started_at, finished_at,
                updated_at, output_artifact_id, error, provider_invocation_id,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_instance_id) DO UPDATE SET
                workflow_id = excluded.workflow_id,
                node_id = excluded.node_id,
                visit = excluded.visit,
                attempt = excluded.attempt,
                node_type = excluded.node_type,
                backend = excluded.backend,
                status = excluded.status,
                decision = excluded.decision,
                started_at = COALESCE(excluded.started_at, node_instances.started_at),
                finished_at = COALESCE(excluded.finished_at, node_instances.finished_at),
                updated_at = excluded.updated_at,
                output_artifact_id = COALESCE(excluded.output_artifact_id, node_instances.output_artifact_id),
                error = COALESCE(excluded.error, node_instances.error),
                provider_invocation_id = COALESCE(excluded.provider_invocation_id, node_instances.provider_invocation_id),
                payload_json = excluded.payload_json
            """,
            (
                node_instance_id,
                workflow_id,
                node_id or str(node.get("id") or ""),
                _int_or(instance.get("visit") or node.get("visits"), 1),
                _int_or(instance.get("attempt"), 1),
                node.get("type") or instance.get("kind"),
                instance.get("backend") or node.get("backend"),
                instance.get("status") or node.get("status"),
                _decision_from_payload(payload),
                _time_value(instance.get("started_at") or node.get("started_at")),
                _time_value(instance.get("finished_at") or node.get("finished_at")),
                _timestamp(),
                output_artifact_id,
                _error_from_payload(payload) or node.get("error"),
                provider_invocation_id,
                _json_dumps({"instance": instance, "node": node, "result": payload}),
            ),
        )
        if provider_invocation_id:
            conn.execute(
                """
                UPDATE provider_invocations
                SET workflow_id = COALESCE(workflow_id, ?),
                    node_instance_id = COALESCE(node_instance_id, ?),
                    node_id = COALESCE(node_id, ?)
                WHERE provider_invocation_id = ?
                """,
                (workflow_id, node_instance_id, node_id, provider_invocation_id),
            )


def _persist_edges(conn: sqlite3.Connection, workflow_id: str, state: dict[str, Any]) -> None:
    for edge in state.get("edges", []) if isinstance(state.get("edges"), list) else []:
        if not isinstance(edge, dict):
            continue
        edge_id = _edge_id(edge)
        transition_id = _stable_id("edge", workflow_id, edge_id)
        conn.execute(
            """
            INSERT INTO edge_transitions (
                transition_id, workflow_id, edge_id, from_node_id, to_node_id,
                condition, condition_result, decision_source, created_at,
                taken_count, skipped_count, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(transition_id) DO UPDATE SET
                condition = excluded.condition,
                condition_result = excluded.condition_result,
                taken_count = excluded.taken_count,
                skipped_count = excluded.skipped_count,
                payload_json = excluded.payload_json
            """,
            (
                transition_id,
                workflow_id,
                edge_id,
                edge.get("from") or edge.get("source"),
                edge.get("to") or edge.get("target"),
                edge.get("when"),
                "taken" if int(edge.get("taken_count") or 0) else "skipped",
                edge.get("label"),
                _timestamp(),
                int(edge.get("taken_count") or 0),
                int(edge.get("skipped_count") or 0),
                _json_dumps(edge),
            ),
        )


def _persist_human_interventions(
    conn: sqlite3.Connection,
    workflow_id: str,
    state: dict[str, Any],
) -> None:
    interventions = state.get("human_interventions")
    if not isinstance(interventions, list):
        return
    for item in interventions:
        if not isinstance(item, dict):
            continue
        intervention_id = str(item.get("id") or item.get("intervention_id") or "")
        if not intervention_id:
            intervention_id = _stable_id("human", workflow_id, _json_dumps(item))
        conn.execute(
            """
            INSERT INTO human_interventions (
                intervention_id, workflow_id, node_instance_id, provider_invocation_id,
                status, requested_by, assignee, created_at, responded_at,
                expires_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(intervention_id) DO UPDATE SET
                status = excluded.status,
                responded_at = COALESCE(excluded.responded_at, human_interventions.responded_at),
                payload_json = excluded.payload_json
            """,
            (
                intervention_id,
                workflow_id,
                item.get("node_instance_id") or item.get("instance_id"),
                item.get("a2a_task_id") or item.get("provider_invocation_id"),
                item.get("status"),
                item.get("source") or item.get("requested_by"),
                item.get("assignee") or item.get("assignee_role"),
                _time_value(item.get("created_at") or item.get("requested_at")),
                _time_value(item.get("responded_at")),
                _time_value(item.get("expires_at")),
                _json_dumps(item),
            ),
        )


def _persist_evaluation_reports(
    conn: sqlite3.Connection,
    workflow_id: str,
    state: dict[str, Any],
    result: dict[str, Any],
) -> None:
    containers = [state, result]
    if isinstance(result.get("state"), dict):
        containers.append(result["state"])
    for container in containers:
        for payload in _result_payloads(container):
            report = payload.get("report") if isinstance(payload.get("report"), dict) else payload
            if not isinstance(report, dict):
                continue
            if not (report.get("benchmark_id") or report.get("single") or report.get("multi")):
                continue
            evaluation_id = str(
                report.get("evaluation_task_id")
                or payload.get("evaluation_task_id")
                or _stable_id("evaluation", workflow_id, _json_dumps(report)[:500])
            )
            single = report.get("single") if isinstance(report.get("single"), dict) else {}
            multi = report.get("multi") if isinstance(report.get("multi"), dict) else {}
            conn.execute(
                """
                INSERT INTO evaluation_reports (
                    evaluation_id, workflow_id, node_instance_id, benchmark_id,
                    single_score, multi_score, winner, score_delta,
                    report_artifact_id, report_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evaluation_id) DO UPDATE SET
                    single_score = excluded.single_score,
                    multi_score = excluded.multi_score,
                    winner = excluded.winner,
                    score_delta = excluded.score_delta,
                    report_json = excluded.report_json
                """,
                (
                    evaluation_id,
                    workflow_id,
                    payload.get("instance_id") or payload.get("node_instance_id"),
                    report.get("benchmark_id"),
                    _float_or(single.get("score") or payload.get("single_score")),
                    _float_or(multi.get("score") or payload.get("multi_score")),
                    report.get("winner") or payload.get("decision"),
                    _float_or(report.get("score_delta") or payload.get("score_delta")),
                    None,
                    _json_dumps(report),
                    _timestamp(),
                ),
            )
            _persist_evaluation_test_cases(conn, evaluation_id, "single", single)
            _persist_evaluation_test_cases(conn, evaluation_id, "multi", multi)


def _persist_evaluation_test_cases(
    conn: sqlite3.Connection,
    evaluation_id: str,
    candidate: str,
    data: dict[str, Any],
) -> None:
    cases = data.get("hidden_test_cases")
    if not isinstance(cases, list):
        return
    for item in cases:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        conn.execute(
            """
            INSERT INTO evaluation_test_cases (
                evaluation_id, candidate, test_name, display_name, status,
                duration_seconds, failure_artifact_id, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(evaluation_id, candidate, test_name) DO UPDATE SET
                display_name = excluded.display_name,
                status = excluded.status,
                duration_seconds = excluded.duration_seconds,
                failure_artifact_id = excluded.failure_artifact_id,
                payload_json = excluded.payload_json
            """,
            (
                evaluation_id,
                candidate,
                item.get("name"),
                item.get("display"),
                item.get("status"),
                _float_or(item.get("duration_seconds")),
                item.get("failure_artifact_id"),
                _json_dumps(item),
            ),
        )


def _persist_a2a_artifact(
    conn: sqlite3.Connection,
    artifact: dict[str, Any],
    *,
    workflow_id: str | None,
    node_instance_id: str | None,
    provider_invocation_id: str | None,
) -> None:
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
    artifact_id = str(
        artifact.get("artifactId")
        or artifact.get("artifact_id")
        or metadata.get("artifactRef")
        or _stable_id("artifact", provider_invocation_id or "", _json_dumps(artifact)[:500])
    )
    _upsert_artifact_metadata(
        conn,
        artifact_id=artifact_id,
        workflow_id=workflow_id,
        node_instance_id=node_instance_id,
        provider_invocation_id=provider_invocation_id,
        kind=str(artifact.get("name") or metadata.get("kind") or "a2a_artifact"),
        uri=metadata.get("artifactUri") or metadata.get("uri"),
        mime_type=_artifact_mime_type(artifact),
        size_bytes=_artifact_size(artifact),
        sha256=metadata.get("contentHash") or metadata.get("sha256"),
        summary=artifact.get("description") or metadata.get("summary"),
        preview=_artifact_preview(artifact),
        metadata=metadata,
    )


def _upsert_artifact_metadata(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    workflow_id: str | None,
    node_instance_id: str | None,
    provider_invocation_id: str | None,
    kind: str,
    uri: Any,
    mime_type: Any,
    size_bytes: Any,
    sha256: Any,
    summary: Any,
    preview: Any,
    metadata: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO artifacts (
            artifact_id, workflow_id, node_instance_id, provider_invocation_id,
            kind, uri, mime_type, size_bytes, sha256, summary, preview,
            created_at, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(artifact_id) DO UPDATE SET
            workflow_id = COALESCE(excluded.workflow_id, artifacts.workflow_id),
            node_instance_id = COALESCE(excluded.node_instance_id, artifacts.node_instance_id),
            provider_invocation_id = COALESCE(excluded.provider_invocation_id, artifacts.provider_invocation_id),
            kind = excluded.kind,
            uri = COALESCE(excluded.uri, artifacts.uri),
            mime_type = COALESCE(excluded.mime_type, artifacts.mime_type),
            size_bytes = COALESCE(excluded.size_bytes, artifacts.size_bytes),
            sha256 = COALESCE(excluded.sha256, artifacts.sha256),
            summary = COALESCE(excluded.summary, artifacts.summary),
            preview = COALESCE(excluded.preview, artifacts.preview),
            metadata_json = excluded.metadata_json
        """,
        (
            artifact_id,
            workflow_id,
            node_instance_id,
            provider_invocation_id,
            kind,
            str(uri) if uri is not None else None,
            str(mime_type) if mime_type is not None else None,
            _int_or(size_bytes, None),
            str(sha256) if sha256 is not None else None,
            str(summary) if summary is not None else None,
            str(preview)[:2000] if preview is not None else None,
            _timestamp(),
            _json_dumps(metadata),
        ),
    )


def _upsert_graph_definition(
    conn: sqlite3.Connection,
    graph_id: str,
    graph_name: str,
    graph_type: Any,
    state: dict[str, Any],
) -> None:
    graph_json = {
        "id": graph_id,
        "name": graph_name,
        "graph_type": graph_type,
        "nodes": state.get("nodes") or [],
        "edges": state.get("edges") or [],
        "levels": state.get("levels") or [],
        "execution_policy": state.get("execution_policy") or {},
    }
    graph_version = _graph_version(state)
    conn.execute(
        """
        INSERT INTO graph_definitions (
            graph_id, graph_version, schema_version, graph_name, graph_type,
            graph_json, content_hash, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(graph_id, graph_version) DO UPDATE SET
            graph_name = excluded.graph_name,
            graph_type = excluded.graph_type,
            graph_json = excluded.graph_json,
            content_hash = excluded.content_hash
        """,
        (
            graph_id,
            graph_version,
            state.get("schema_version") or state.get("schemaVersion"),
            graph_name,
            graph_type,
            _json_dumps(graph_json),
            _sha256_json(graph_json),
            _timestamp(),
        ),
    )


def _upsert_workflow_stub(conn: sqlite3.Connection, workflow_id: str, updated_at: str | None) -> None:
    conn.execute(
        """
        INSERT INTO workflow_runs (
            workflow_id, graph_id, graph_name, status, updated_at, source, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workflow_id) DO UPDATE SET
            updated_at = COALESCE(excluded.updated_at, workflow_runs.updated_at)
        """,
        (workflow_id, workflow_id, workflow_id, "unknown", updated_at, "provider_projection", _timestamp()),
    )


def _insert_event(
    conn: sqlite3.Connection,
    *,
    workflow_id: str,
    event_type: str,
    payload: dict[str, Any],
    event_time: str,
    node_instance_id: str | None = None,
    provider_invocation_id: str | None = None,
) -> None:
    event_id = _stable_id("event", workflow_id, event_type, event_time, _json_dumps(payload)[:500])
    conn.execute(
        """
        INSERT OR IGNORE INTO execution_events (
            event_id, workflow_id, node_instance_id, provider_invocation_id,
            event_type, event_time, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            workflow_id,
            node_instance_id,
            provider_invocation_id,
            event_type,
            event_time,
            _json_dumps(payload),
        ),
    )


@contextmanager
def _connection():
    path = Path(execution_store_db_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_definitions (
            graph_id TEXT NOT NULL,
            graph_version TEXT NOT NULL DEFAULT 'v1',
            schema_version TEXT,
            graph_name TEXT,
            graph_type TEXT,
            graph_json TEXT NOT NULL,
            content_hash TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (graph_id, graph_version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            workflow_id TEXT PRIMARY KEY,
            graph_id TEXT,
            graph_name TEXT,
            graph_version TEXT,
            status TEXT,
            submitted_at TEXT,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT,
            temporal_workflow_id TEXT,
            input_artifact_id TEXT,
            final_result_artifact_id TEXT,
            error TEXT,
            snapshot_json TEXT,
            source TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS node_instances (
            node_instance_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            visit INTEGER NOT NULL DEFAULT 1,
            attempt INTEGER NOT NULL DEFAULT 1,
            node_type TEXT,
            backend TEXT,
            status TEXT,
            decision TEXT,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT,
            input_artifact_id TEXT,
            output_artifact_id TEXT,
            error TEXT,
            provider_invocation_id TEXT,
            payload_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS edge_transitions (
            transition_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            from_node_instance_id TEXT,
            to_node_id TEXT,
            edge_id TEXT,
            from_node_id TEXT,
            condition TEXT,
            condition_result TEXT,
            decision_source TEXT,
            created_at TEXT NOT NULL,
            taken_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_invocations (
            provider_invocation_id TEXT PRIMARY KEY,
            workflow_id TEXT,
            node_instance_id TEXT,
            node_id TEXT,
            provider TEXT,
            external_task_id TEXT,
            idempotency_key TEXT,
            status TEXT,
            created_at TEXT,
            submitted_at TEXT,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT,
            last_heartbeat_at TEXT,
            input_artifact_id TEXT,
            output_artifact_id TEXT,
            trace_artifact_id TEXT,
            error TEXT,
            task_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY,
            workflow_id TEXT,
            node_instance_id TEXT,
            provider_invocation_id TEXT,
            kind TEXT,
            uri TEXT,
            mime_type TEXT,
            size_bytes INTEGER,
            sha256 TEXT,
            summary TEXT,
            preview TEXT,
            created_at TEXT NOT NULL,
            metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_events (
            event_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            node_instance_id TEXT,
            provider_invocation_id TEXT,
            event_type TEXT NOT NULL,
            event_time TEXT NOT NULL,
            payload_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS human_interventions (
            intervention_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            node_instance_id TEXT,
            provider_invocation_id TEXT,
            status TEXT,
            request_artifact_id TEXT,
            response_artifact_id TEXT,
            requested_by TEXT,
            assignee TEXT,
            created_at TEXT,
            responded_at TEXT,
            expires_at TEXT,
            payload_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evaluation_reports (
            evaluation_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            node_instance_id TEXT,
            benchmark_id TEXT,
            single_source_workflow_id TEXT,
            multi_source_workflow_id TEXT,
            single_score REAL,
            multi_score REAL,
            winner TEXT,
            score_delta REAL,
            report_artifact_id TEXT,
            report_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evaluation_test_cases (
            evaluation_id TEXT NOT NULL,
            candidate TEXT NOT NULL,
            test_name TEXT NOT NULL,
            display_name TEXT,
            status TEXT,
            duration_seconds REAL,
            failure_artifact_id TEXT,
            payload_json TEXT,
            PRIMARY KEY (evaluation_id, candidate, test_name)
        )
        """
    )
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status)",
        "CREATE INDEX IF NOT EXISTS idx_workflow_runs_graph ON workflow_runs(graph_id)",
        "CREATE INDEX IF NOT EXISTS idx_node_instances_workflow ON node_instances(workflow_id)",
        "CREATE INDEX IF NOT EXISTS idx_node_instances_node ON node_instances(node_id)",
        "CREATE INDEX IF NOT EXISTS idx_provider_invocations_workflow ON provider_invocations(workflow_id)",
        "CREATE INDEX IF NOT EXISTS idx_provider_invocations_node ON provider_invocations(node_id)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_workflow ON artifacts(workflow_id)",
        "CREATE INDEX IF NOT EXISTS idx_execution_events_workflow ON execution_events(workflow_id, event_time)",
    ):
        conn.execute(sql)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, _timestamp()),
    )
    conn.commit()


def _instance_results(state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    for container in (state, result, result.get("state") if isinstance(result.get("state"), dict) else {}):
        if isinstance(container, dict) and isinstance(container.get("instance_results"), dict):
            return container["instance_results"]
    return {}


def _result_payloads(container: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for key in ("results", "instance_results"):
        value = container.get(key)
        if isinstance(value, dict):
            payloads.extend(item for item in value.values() if isinstance(item, dict))
    return payloads


def _instance_from_node(node: dict[str, Any]) -> dict[str, Any]:
    node_id = str(node.get("id") or "")
    return {
        "id": node.get("current_instance_id") or f"{node_id}#{node.get('visits') or 1}",
        "node_id": node_id,
        "visit": node.get("visits") or 1,
        "status": node.get("status"),
        "backend": node.get("backend"),
        "started_at": node.get("started_at"),
        "finished_at": node.get("finished_at"),
        "a2a_task_id": node.get("a2a_task_id"),
        "agent_service_task_id": node.get("agent_service_task_id"),
        "summary": node.get("summary"),
    }


def _provider_invocation_id_from_instance(instance: dict[str, Any], node: dict[str, Any]) -> str | None:
    for key in ("a2a_task_id", "claude_task_id", "codex_task_id", "agent_service_task_id", "hermes_job_id"):
        value = instance.get(key) or node.get(key)
        if value:
            return str(value)
    return None


def _first_artifact_id(task: dict[str, Any]) -> str | None:
    artifacts = task.get("artifacts") if isinstance(task.get("artifacts"), list) else []
    for artifact in artifacts:
        if isinstance(artifact, dict):
            value = artifact.get("artifactId") or artifact.get("artifact_id")
            if value:
                return str(value)
    return None


def _artifact_mime_type(artifact: dict[str, Any]) -> str | None:
    parts = artifact.get("parts") if isinstance(artifact.get("parts"), list) else []
    for part in parts:
        if isinstance(part, dict) and part.get("mediaType"):
            return str(part["mediaType"])
    return None


def _artifact_size(artifact: dict[str, Any]) -> int | None:
    return len(_json_dumps(artifact).encode("utf-8"))


def _artifact_preview(artifact: dict[str, Any]) -> str:
    return _json_dumps(artifact)[:2000]


def _edge_id(edge: dict[str, Any]) -> str:
    return str(
        edge.get("id")
        or f"{edge.get('from') or edge.get('source')}->{edge.get('to') or edge.get('target')}:{edge.get('label') or edge.get('when') or ''}"
    )


def _decision_from_payload(payload: Any) -> str | None:
    if isinstance(payload, dict):
        value = payload.get("decision") or payload.get("route")
        return str(value) if value is not None else None
    return None


def _error_from_payload(payload: Any) -> str | None:
    if isinstance(payload, dict) and payload.get("error") is not None:
        return _json_or_text(payload.get("error"))
    return None


def _status_message(task: dict[str, Any]) -> str | None:
    status = task.get("status") if isinstance(task.get("status"), dict) else {}
    message = status.get("message")
    return _json_or_text(message) if message else None


def _finished_at_from_task(task: dict[str, Any], status: str) -> str | None:
    for key in ("finished_at", "archived_at", "updated_at"):
        value = _time_value(task.get(key))
        if value and status in {"completed", "failed", "cancelled", "terminated", "timed_out"}:
            return value
    return None


def _graph_version(state: dict[str, Any]) -> str:
    return str(state.get("graph_version") or state.get("graphVersion") or "v1")


def _time_value(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(value)))
        except (OverflowError, ValueError):
            return str(value)
    return str(value)


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        payload = json.loads(value)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_or_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return _json_dumps(value)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_json_dumps(value).encode("utf-8")).hexdigest()


def _stable_id(*parts: Any) -> str:
    digest = hashlib.sha256(
        "\x1f".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()
    return digest


def _int_or(value: Any, fallback: int | None = 0) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _float_or(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
