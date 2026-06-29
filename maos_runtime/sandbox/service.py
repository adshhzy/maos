import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from maos_runtime.a2a import complete_task_from_agent_callback
from maos_runtime.dag_workflow import ACTIVITIES, JsonDagWorkflow, validate_graph
from maos_runtime.sandbox.api_projection import (
    _completed_workflow_display_limit,
    _minimal_task,
    _normalize_task_status,
    _slim_result_for_api,
    _slim_state_for_api,
    _state_from_result,
    _status_from_execution,
    _status_from_raw_description,
    _task_from_state,
    _task_list_item_for_api,
    _truthy_env,
)
from maos_runtime.sandbox.constants import (
    DEFAULT_TEMPORAL_DB_FILE,
    DEFAULT_TEMPORAL_HOST,
    DEFAULT_TEMPORAL_PORT,
    DEFAULT_TEMPORAL_UI_PORT,
    TASK_QUEUE,
    WORKFLOW_LIST_LIMIT,
    WORKFLOW_LIST_QUERY,
    WORKFLOW_QUERY_TIMEOUT_SECONDS,
    WORKFLOW_RESULT_TIMEOUT_SECONDS,
)
from maos_runtime.sandbox.identifiers import _task_id_for_graph
from maos_runtime.sandbox.task_archive import (
    load_archived_task,
    merge_visible_tasks,
    prefer_archived_task,
)


class TemporalTaskService:
    """Sandbox 微服务与 Temporal 之间的桥接层。

    这里不是重新实现一套内存任务管理器。Web/API 线程调用本类的同步方法，
    内部事件循环负责连接 Temporal、启动 workflow、查询状态，并把 Agent
    callback 转成 workflow signal。
    """

    def __init__(
        self,
        *,
        temporal_address: str | None = None,
        temporal_namespace: str = "default",
        temporal_host: str = DEFAULT_TEMPORAL_HOST,
        temporal_port: int = DEFAULT_TEMPORAL_PORT,
        temporal_ui: bool = True,
        temporal_ui_port: int = DEFAULT_TEMPORAL_UI_PORT,
        temporal_db_file: str | Path | None = DEFAULT_TEMPORAL_DB_FILE,
        start_worker: bool = True,
    ) -> None:
        self._lock = threading.Lock()
        self._runtime_status = "starting"
        self._error: str | None = None
        self._loop_ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Client | None = None
        self._env: WorkflowEnvironment | None = None
        self._worker: Worker | None = None
        self._temporal_address = temporal_address
        self._temporal_namespace = temporal_namespace
        self._temporal_host = temporal_host
        self._temporal_port = temporal_port
        self._temporal_ui = temporal_ui
        self._temporal_ui_port = temporal_ui_port
        self._temporal_db_file = None if temporal_db_file is None else Path(temporal_db_file)
        self._start_worker = start_worker
        self._temporal_mode = "external" if temporal_address else "embedded-persistent-dev"
        self._service_started_at = time.time()
        self._show_workflow_history = _truthy_env("SANDBOX_SHOW_WORKFLOW_HISTORY")
        self._temporal_target = (
            temporal_address if temporal_address else f"{temporal_host}:{temporal_port}"
        )
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def submit_batch(self, graphs: list[dict[str, Any]]) -> list[str]:
        if not graphs:
            raise ValueError("No graphs submitted")
        for graph in graphs:
            validate_graph(graph)
        return self._run_coro(self._submit_batch(graphs), timeout=30)

    def snapshot(self) -> dict[str, Any]:
        if not self._is_ready():
            return self._empty_snapshot()
        return self._run_coro(self._snapshot(), timeout=60)

    def task_snapshot(self, task_id: str) -> dict[str, Any]:
        if not self._is_ready():
            raise KeyError(task_id)
        return self._run_coro(self._task_snapshot(task_id), timeout=30)

    def runtime_info(self) -> dict[str, Any]:
        with self._lock:
            return {
                "runtime_status": self._runtime_status,
                "error": self._error,
                "temporal": self._temporal_info(),
            }

    def handle_agent_callback(self, callback: dict[str, Any]) -> dict[str, Any]:
        # Agent/Simulator 的回调先规范化为 A2A event，再 signal 给对应 workflow。
        if not self._is_ready():
            raise RuntimeError("Temporal runtime is not ready")
        event = complete_task_from_agent_callback(callback)
        self._run_coro(self._signal_agent_event(event), timeout=30)
        return event

    def human_interventions(
        self,
        task_id: str | None = None,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if task_id:
            task = self.task_snapshot(task_id)
            items = list((task.get("state") or {}).get("human_interventions") or [])
        else:
            snapshot = self.snapshot()
            items = []
            for task in snapshot.get("tasks", []):
                for item in (task.get("state") or {}).get("human_interventions") or []:
                    enriched = dict(item)
                    enriched.setdefault("workflow_id", task.get("workflow_id") or task.get("task_id"))
                    enriched.setdefault("task_id", task.get("task_id"))
                    enriched.setdefault("graph_name", task.get("graph_name"))
                    items.append(enriched)
        if status:
            normalized = status.lower()
            items = [item for item in items if str(item.get("status", "")).lower() == normalized]
        return items

    def submit_human_response(
        self,
        task_id: str,
        intervention_id: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._is_ready():
            raise RuntimeError("Temporal runtime is not ready")
        event = {
            **response,
            "workflow_id": task_id,
            "task_id": task_id,
            "intervention_id": intervention_id,
            "status": "resolved",
        }
        self._run_coro(self._signal_human_response(task_id, event), timeout=30)
        return event

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start_temporal())
        self._loop_ready.set()
        self._loop.run_forever()

    async def _start_temporal(self) -> None:
        try:
            if self._temporal_address:
                self._client = await Client.connect(
                    self._temporal_address,
                    namespace=self._temporal_namespace,
                )
            else:
                db_filename = None
                if self._temporal_db_file is not None:
                    self._temporal_db_file.parent.mkdir(parents=True, exist_ok=True)
                    db_filename = str(self._temporal_db_file)
                self._env = await WorkflowEnvironment.start_local(
                    namespace=self._temporal_namespace,
                    ip=self._temporal_host,
                    port=self._temporal_port,
                    ui=self._temporal_ui,
                    ui_port=self._temporal_ui_port if self._temporal_ui else None,
                    dev_server_database_filename=db_filename,
                )
                self._client = self._env.client
            if self._start_worker:
                self._worker = Worker(
                    self._client,
                    task_queue=TASK_QUEUE,
                    workflows=[JsonDagWorkflow],
                    activities=ACTIVITIES,
                )
                await self._worker.__aenter__()
            with self._lock:
                self._runtime_status = "ready"
        except Exception as exc:
            with self._lock:
                self._runtime_status = "failed"
                self._error = str(exc)
            self._loop_ready.set()
            raise

    async def _submit_batch(self, graphs: list[dict[str, Any]]) -> list[str]:
        if self._client is None:
            raise RuntimeError("Temporal client is not ready")

        task_ids: list[str] = []
        for graph in graphs:
            # 每个任务图对应一个 Temporal workflow；长等待和高并发由 Temporal 承担。
            task_id = _task_id_for_graph(graph)
            await self._client.start_workflow(
                JsonDagWorkflow.run,
                graph,
                id=task_id,
                task_queue=TASK_QUEUE,
                memo={
                    "service": "persistent-execution-sandbox",
                    "graph_id": graph["id"],
                    "graph_name": graph.get("name", graph["id"]),
                },
                static_summary=f"{graph.get('name', graph['id'])} ({graph['id']})",
            )
            task_ids.append(task_id)
        return task_ids

    async def _signal_agent_event(self, event: dict[str, Any]) -> None:
        workflow_id = event.get("workflow_id")
        if not workflow_id:
            raise ValueError("Agent callback did not include workflow_id")
        if self._client is None:
            raise RuntimeError("Temporal client is not ready")
        handle = self._client.get_workflow_handle(workflow_id)
        await handle.signal(JsonDagWorkflow.agent_node_completed, event)

    async def _signal_human_response(self, workflow_id: str, event: dict[str, Any]) -> None:
        if self._client is None:
            raise RuntimeError("Temporal client is not ready")
        handle = self._client.get_workflow_handle(workflow_id)
        await handle.signal(JsonDagWorkflow.human_intervention_resolved, event)

    async def _snapshot(self) -> dict[str, Any]:
        if self._client is None:
            return self._empty_snapshot()
        executions = await self._list_dag_workflows()
        tasks = list(
            await asyncio.gather(
                *(self._task_from_execution(execution) for execution in executions)
            )
        )
        running_count = sum(
            1 for task in tasks if task["status"] in {"starting", "running"}
        )
        with self._lock:
            self._runtime_status = "running" if running_count else "ready"
            runtime_status = self._runtime_status
            error = self._error
        return {
            "runtime_status": runtime_status,
            "error": error,
            "temporal": self._temporal_info(),
            "tasks": merge_visible_tasks(tasks),
        }

    async def _task_snapshot(self, task_id: str) -> dict[str, Any]:
        if self._client is None:
            raise KeyError(task_id)
        handle = self._client.get_workflow_handle(task_id)
        try:
            description = await handle.describe()
        except Exception as exc:
            archived = load_archived_task(task_id)
            if archived:
                return archived
            raise KeyError(task_id) from exc

        status = _status_from_raw_description(description)
        if status in {"completed", "failed", "cancelled", "terminated", "timed_out"}:
            task = await self._closed_task_from_handle(task_id, handle, status)
        else:
            task = await self._running_task_from_handle(task_id, handle)
        return prefer_archived_task(task, load_archived_task(task_id))

    async def _list_dag_workflows(self) -> list[Any]:
        assert self._client is not None
        executions: list[Any] = []
        try:
            iterator = self._client.list_workflows(
                WORKFLOW_LIST_QUERY,
                limit=WORKFLOW_LIST_LIMIT,
            )
            async for execution in iterator:
                executions.append(execution)
        except Exception:
            iterator = self._client.list_workflows(limit=WORKFLOW_LIST_LIMIT)
            async for execution in iterator:
                if execution.workflow_type == "JsonDagWorkflow":
                    executions.append(execution)
        executions = sorted(executions, key=lambda item: item.start_time, reverse=True)
        if not self._show_workflow_history:
            executions = self._visible_recent_executions(executions)
        return executions

    def _visible_recent_executions(self, executions: list[Any]) -> list[Any]:
        visible: list[Any] = []
        completed_count = 0
        completed_limit = _completed_workflow_display_limit()
        for execution in executions:
            status = _status_from_execution(execution)
            is_recently_submitted = (
                execution.start_time.timestamp() >= self._service_started_at - 5
            )
            if status == "running" or is_recently_submitted:
                visible.append(execution)
                if status == "completed":
                    completed_count += 1
                continue
            if status == "completed" and completed_count < completed_limit:
                visible.append(execution)
                completed_count += 1
        return visible

    async def _task_from_execution(self, execution: Any) -> dict[str, Any]:
        assert self._client is not None
        handle = self._client.get_workflow_handle(execution.id, run_id=execution.run_id)
        status = _status_from_execution(execution)
        if status in {"completed", "failed", "cancelled", "terminated", "timed_out"}:
            task = await self._closed_task_from_handle(execution.id, handle, status)
        else:
            task = await self._running_task_from_handle(execution.id, handle)
        task["submitted_at"] = execution.start_time.timestamp()
        task["workflow_id"] = execution.id
        task["task_id"] = execution.id
        return task

    async def _running_task_from_handle(self, task_id: str, handle: Any) -> dict[str, Any]:
        try:
            state = await asyncio.wait_for(
                handle.query(JsonDagWorkflow.graph_state),
                timeout=WORKFLOW_QUERY_TIMEOUT_SECONDS,
            )
            status = _normalize_task_status(state.get("workflow_status", "running"))
            if status in {"completed", "failed", "cancelled", "terminated", "timed_out"}:
                try:
                    result = await asyncio.wait_for(
                        handle.result(),
                        timeout=WORKFLOW_RESULT_TIMEOUT_SECONDS,
                    )
                    return _task_from_state(
                        task_id,
                        result.get("state") or state,
                        status=result.get("status", status),
                        result=_slim_result_for_api(result),
                    )
                except Exception as exc:
                    return _task_from_state(
                        task_id,
                        state,
                        status="failed",
                        error=str(exc),
                    )
            return _task_from_state(task_id, state, status=status)
        except Exception as exc:
            error = str(exc)
            status = "failed" if "Workflow Task in failed state" in error else "running"
            return _minimal_task(task_id, status=status, error=error)

    async def _closed_task_from_handle(
        self,
        task_id: str,
        handle: Any,
        status: str,
    ) -> dict[str, Any]:
        try:
            result = await asyncio.wait_for(
                handle.result(),
                timeout=WORKFLOW_RESULT_TIMEOUT_SECONDS,
            )
            state = result.get("state") or _state_from_result(result)
            return _task_from_state(
                task_id,
                state,
                status=result.get("status", status),
                result=_slim_result_for_api(result),
            )
        except Exception as exc:
            return _minimal_task(task_id, status="failed", error=str(exc))

    def _run_coro(self, coro: Any, timeout: float) -> Any:
        self._loop_ready.wait(timeout=60)
        if self._loop is None:
            raise RuntimeError("Temporal runtime loop is not ready")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def _is_ready(self) -> bool:
        self._loop_ready.wait(timeout=60)
        with self._lock:
            return self._runtime_status != "failed" and self._loop is not None

    def _empty_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "runtime_status": self._runtime_status,
                "error": self._error,
                "temporal": self._temporal_info(),
                "tasks": [],
            }

    def _temporal_info(self) -> dict[str, Any]:
        return {
            "mode": self._temporal_mode,
            "target": self._temporal_target,
            "namespace": self._temporal_namespace,
            "db_file": (
                str(self._temporal_db_file)
                if self._temporal_mode != "external" and self._temporal_db_file
                else None
            ),
            "ui": self._temporal_ui if self._temporal_mode != "external" else None,
            "ui_port": self._temporal_ui_port if self._temporal_ui else None,
            "task_queue": TASK_QUEUE,
            "worker": "enabled" if self._start_worker else "disabled",
        }


# Preferred name for the Temporal-backed control-flow sandbox.
ControlFlowTaskService = TemporalTaskService

# Backwards-compatible name used by older imports.
DagTaskManager = TemporalTaskService






































