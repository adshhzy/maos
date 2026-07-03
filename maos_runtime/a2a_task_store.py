"""Persistent A2A provider task store.

Provider tasks used to live only in the process-local ``TASKS`` dict and in the
legacy ``a2a-invocations.json`` mirror. The dict is still exposed for
compatibility with the existing provider implementations, but SQLite is now the
durable source used for idempotency recovery and task snapshots.
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from maos_runtime.persistence import persist_provider_task_record


class PersistentTaskCache(dict[str, dict[str, Any]]):
    """Compatibility cache that mirrors direct task assignments to SQLite."""

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        super().__setitem__(key, value)
        if isinstance(value, dict) and isinstance(value.get("task"), dict):
            _persist_record(str(key), value)

    def get(self, key: str, default: Any = None) -> Any:
        if dict.__contains__(self, key):
            return super().get(key, default)
        record = _load_task_record_by_id(str(key))
        if record is None:
            return default
        dict.__setitem__(self, str(key), copy.deepcopy(record))
        return super().get(str(key), default)

    def __getitem__(self, key: str) -> dict[str, Any]:
        if not dict.__contains__(self, key):
            record = _load_task_record_by_id(str(key))
            if record is None:
                raise KeyError(key)
            dict.__setitem__(self, str(key), copy.deepcopy(record))
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        if super().__contains__(key):
            return True
        if not isinstance(key, str):
            return False
        record = _load_task_record_by_id(key)
        if record is None:
            return False
        dict.__setitem__(self, key, copy.deepcopy(record))
        return True

    def values(self):  # type: ignore[override]
        _hydrate_cache_from_sqlite()
        return super().values()

    def clear(self) -> None:
        super().clear()
        if _truthy_env("A2A_TASK_STORE_CLEAR_SQLITE_ON_CACHE_CLEAR"):
            clear_persistent_tasks()


TASKS: PersistentTaskCache = PersistentTaskCache()
TASKS_LOCK = threading.RLock()
_STORE_LOCK = threading.RLock()
_INVOCATIONS_LOCK = threading.RLock()


def store_task(task: dict[str, Any]) -> None:
    record = {
        "task": copy.deepcopy(task),
        "result_artifact_created": False,
    }
    with TASKS_LOCK:
        TASKS[task["id"]] = copy.deepcopy(record)
    store_idempotent_task(task)


def request_idempotency_key(request: dict[str, Any]) -> str | None:
    value = request.get("metadata", {}).get("idempotency_key")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def task_for_idempotency_key(idempotency_key: str | None) -> dict[str, Any] | None:
    if not idempotency_key:
        return None
    with TASKS_LOCK:
        for record in TASKS.values():
            task = record.get("task", {})
            if task.get("metadata", {}).get("idempotencyKey") == idempotency_key:
                return copy.deepcopy(task)

    record = _load_task_record_by_idempotency_key(idempotency_key)
    if record is None and _truthy_env("A2A_ENABLE_JSON_INVOCATION_FALLBACK"):
        record = _load_invocation_record(idempotency_key)
        if record is not None:
            _persist_invocation_record(idempotency_key, record)
    task = record.get("task") if isinstance(record, dict) else None
    if not isinstance(task, dict):
        return None
    with TASKS_LOCK:
        dict.__setitem__(
            TASKS,
            task["id"],
            {
                "task": copy.deepcopy(task),
                "result_artifact_created": bool(record.get("result_artifact_created", False)),
            },
        )
    return copy.deepcopy(task)


def store_idempotent_task(task: dict[str, Any]) -> None:
    idempotency_key = task.get("metadata", {}).get("idempotencyKey")
    if not idempotency_key:
        _persist_task_without_idempotency(task)
        return

    task_copy = copy.deepcopy(task)
    previous = _load_task_record_by_idempotency_key(str(idempotency_key)) or {}
    if not previous and _truthy_env("A2A_ENABLE_JSON_INVOCATION_FALLBACK"):
        previous = _load_invocation_record(str(idempotency_key)) or {}
    previous_task = previous.get("task") if isinstance(previous, dict) else None
    previous_artifacts = (
        previous_task.get("artifacts")
        if isinstance(previous_task, dict) and isinstance(previous_task.get("artifacts"), list)
        else []
    )
    current_artifacts = task_copy.get("artifacts") if isinstance(task_copy.get("artifacts"), list) else []
    if previous_artifacts and not current_artifacts:
        task_copy["artifacts"] = copy.deepcopy(previous_artifacts)
        current_artifacts = task_copy["artifacts"]
    artifact_created = bool(current_artifacts) or bool(previous.get("result_artifact_created", False))
    record = {
        **previous,
        "idempotency_key": str(idempotency_key),
        "workflow_id": task_copy.get("metadata", {}).get("workflow_id"),
        "node_id": task_copy.get("metadata", {}).get("nodeId"),
        "backend": task_copy.get("metadata", {}).get("backend"),
        "a2a_task_id": task_copy.get("id"),
        "status": task_copy.get("status", {}).get("state"),
        "created_at": previous.get("created_at") or _timestamp(),
        "updated_at": _timestamp(),
        "result_artifact_created": artifact_created,
        "task": task_copy,
    }
    _persist_invocation_record(str(idempotency_key), record)
    with TASKS_LOCK:
        dict.__setitem__(
            TASKS,
            task_copy["id"],
            {
                "task": copy.deepcopy(task_copy),
                "result_artifact_created": artifact_created,
            },
        )
    _write_json_registry_record(str(idempotency_key), record)


def load_invocation_records() -> dict[str, Any]:
    """Return idempotency-keyed provider task records from SQLite.

    Set ``A2A_ENABLE_JSON_INVOCATION_FALLBACK=1`` only for one-off legacy
    recovery from an old ``a2a-invocations.json`` file.
    """

    records = _load_sqlite_invocations()
    if _truthy_env("A2A_ENABLE_JSON_INVOCATION_FALLBACK"):
        json_records = _load_invocations_json_unlocked()
        for key, value in json_records.items():
            records.setdefault(key, value)
    return records


def task_record_for_provider_task_id(task_id: str) -> dict[str, Any] | None:
    """Load a provider task record by A2A/provider task id from SQLite."""

    return _load_task_record_by_id(task_id)


def provider_task_db_file() -> str:
    """Public path helper for diagnostics and Web UI metadata."""

    return str(_provider_task_db_file())


def clear_persistent_tasks() -> None:
    with _STORE_LOCK, _connection() as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM provider_tasks")
        conn.execute("DELETE FROM provider_events")
        conn.execute("DELETE FROM provider_artifacts")
        conn.commit()


def _persist_task_without_idempotency(task: dict[str, Any]) -> None:
    record = {"task": copy.deepcopy(task), "result_artifact_created": bool(task.get("artifacts"))}
    _persist_record(str(task["id"]), record)
    with TASKS_LOCK:
        dict.__setitem__(TASKS, str(task["id"]), copy.deepcopy(record))


def _persist_record(task_id: str, record: dict[str, Any]) -> None:
    task = record.get("task")
    if not isinstance(task, dict):
        return
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    idempotency_key = metadata.get("idempotencyKey")
    payload = _record_payload(
        task=task,
        idempotency_key=str(idempotency_key) if idempotency_key else None,
        result_artifact_created=bool(record.get("result_artifact_created", False)),
        existing_created_at=None,
    )
    _upsert_provider_task(payload)


def _persist_invocation_record(idempotency_key: str, record: dict[str, Any]) -> None:
    task = record.get("task")
    if not isinstance(task, dict):
        return
    payload = _record_payload(
        task=task,
        idempotency_key=idempotency_key,
        result_artifact_created=bool(record.get("result_artifact_created", False)),
        existing_created_at=record.get("created_at"),
    )
    _upsert_provider_task(payload)


def _record_payload(
    *,
    task: dict[str, Any],
    idempotency_key: str | None,
    result_artifact_created: bool,
    existing_created_at: Any,
) -> dict[str, Any]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    status = task.get("status") if isinstance(task.get("status"), dict) else {}
    now = _timestamp()
    return {
        "provider_task_id": task.get("id"),
        "idempotency_key": idempotency_key,
        "workflow_id": metadata.get("workflow_id"),
        "node_id": metadata.get("nodeId"),
        "backend": metadata.get("backend"),
        "status": status.get("state"),
        "created_at": existing_created_at or metadata.get("createdAt") or metadata.get("startedAt") or now,
        "updated_at": now,
        "finished_at": metadata.get("finishedAt"),
        "result_artifact_created": 1 if result_artifact_created else 0,
        "task_json": json.dumps(task, ensure_ascii=False),
    }


def _upsert_provider_task(payload: dict[str, Any]) -> None:
    if not payload.get("provider_task_id"):
        return
    with _STORE_LOCK, _connection() as conn:
        _ensure_schema(conn)
        previous = conn.execute(
            "SELECT created_at FROM provider_tasks WHERE provider_task_id = ?",
            (payload["provider_task_id"],),
        ).fetchone()
        created_at = previous["created_at"] if previous and previous["created_at"] else payload["created_at"]
        conn.execute(
            """
            INSERT INTO provider_tasks (
                provider_task_id, idempotency_key, workflow_id, node_id, backend,
                status, created_at, updated_at, finished_at,
                result_artifact_created, task_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_task_id) DO UPDATE SET
                idempotency_key = excluded.idempotency_key,
                workflow_id = excluded.workflow_id,
                node_id = excluded.node_id,
                backend = excluded.backend,
                status = excluded.status,
                updated_at = excluded.updated_at,
                finished_at = excluded.finished_at,
                result_artifact_created = excluded.result_artifact_created,
                task_json = excluded.task_json
            """,
            (
                payload["provider_task_id"],
                payload.get("idempotency_key"),
                payload.get("workflow_id"),
                payload.get("node_id"),
                payload.get("backend"),
                payload.get("status"),
                created_at,
                payload.get("updated_at"),
                payload.get("finished_at"),
                payload.get("result_artifact_created"),
                payload.get("task_json"),
            ),
        )
        conn.commit()
    _project_provider_task_payload(payload, created_at)


def _project_provider_task_payload(payload: dict[str, Any], created_at: Any) -> None:
    try:
        task = json.loads(payload.get("task_json") or "{}")
    except Exception:
        return
    if not isinstance(task, dict):
        return
    record = {
        "idempotency_key": payload.get("idempotency_key"),
        "workflow_id": payload.get("workflow_id"),
        "node_id": payload.get("node_id"),
        "backend": payload.get("backend"),
        "a2a_task_id": payload.get("provider_task_id"),
        "status": payload.get("status"),
        "created_at": created_at,
        "updated_at": payload.get("updated_at"),
        "finished_at": payload.get("finished_at"),
        "result_artifact_created": bool(payload.get("result_artifact_created")),
        "task": task,
    }
    try:
        persist_provider_task_record(record)
    except Exception:
        return


def _load_task_record_by_id(task_id: str) -> dict[str, Any] | None:
    with _STORE_LOCK, _connection() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM provider_tasks WHERE provider_task_id = ?",
            (task_id,),
        ).fetchone()
    return _record_from_row(row) if row else None


def _load_task_record_by_idempotency_key(idempotency_key: str) -> dict[str, Any] | None:
    with _STORE_LOCK, _connection() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM provider_tasks WHERE idempotency_key = ? ORDER BY updated_at DESC LIMIT 1",
            (idempotency_key,),
        ).fetchone()
    return _record_from_row(row) if row else None


def _load_sqlite_invocations() -> dict[str, Any]:
    with _STORE_LOCK, _connection() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM provider_tasks WHERE idempotency_key IS NOT NULL"
        ).fetchall()
    records: dict[str, Any] = {}
    for row in rows:
        record = _record_from_row(row)
        if not record:
            continue
        key = record.get("idempotency_key")
        if key:
            records[str(key)] = record
    return records


def _record_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        task = json.loads(row["task_json"])
    except Exception:
        return None
    return {
        "idempotency_key": row["idempotency_key"],
        "workflow_id": row["workflow_id"],
        "node_id": row["node_id"],
        "backend": row["backend"],
        "a2a_task_id": row["provider_task_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
        "result_artifact_created": bool(row["result_artifact_created"]),
        "task": task,
    }


def _hydrate_cache_from_sqlite() -> None:
    with _STORE_LOCK, _connection() as conn:
        _ensure_schema(conn)
        rows = conn.execute("SELECT * FROM provider_tasks").fetchall()
    for row in rows:
        record = _record_from_row(row)
        if record and record.get("a2a_task_id") and not dict.__contains__(TASKS, record["a2a_task_id"]):
            dict.__setitem__(
                TASKS,
                str(record["a2a_task_id"]),
                {
                    "task": copy.deepcopy(record["task"]),
                    "result_artifact_created": bool(record.get("result_artifact_created", False)),
                },
            )


def _load_invocation_record(idempotency_key: str) -> dict[str, Any] | None:
    with _INVOCATIONS_LOCK:
        return copy.deepcopy(_load_invocations_json_unlocked().get(idempotency_key))


def _write_json_registry_record(idempotency_key: str, record: dict[str, Any]) -> None:
    if _truthy_env("A2A_DISABLE_JSON_INVOCATION_MIRROR"):
        return
    with _INVOCATIONS_LOCK:
        registry = _load_invocations_json_unlocked()
        registry[idempotency_key] = copy.deepcopy(record)
        _write_invocations_json_unlocked(registry)


def _load_invocations_unlocked() -> dict[str, Any]:
    """Compatibility helper used by older code/tests."""

    return load_invocation_records()


def _write_invocations_unlocked(registry: dict[str, Any]) -> None:
    """Compatibility helper that writes the JSON mirror only."""

    _write_invocations_json_unlocked(registry)


def _load_invocations_json_unlocked() -> dict[str, Any]:
    path = _invocation_registry_file()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_invocations_json_unlocked(registry: dict[str, Any]) -> None:
    path = _invocation_registry_file()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def _connect() -> sqlite3.Connection:
    path = _provider_task_db_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def _connection():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_tasks (
            provider_task_id TEXT PRIMARY KEY,
            idempotency_key TEXT UNIQUE,
            workflow_id TEXT,
            node_id TEXT,
            backend TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            finished_at TEXT,
            result_artifact_created INTEGER NOT NULL DEFAULT 0,
            task_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_task_id TEXT NOT NULL,
            event_type TEXT,
            event_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_task_id TEXT NOT NULL,
            artifact_id TEXT,
            artifact_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_tasks_workflow ON provider_tasks(workflow_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_tasks_node ON provider_tasks(node_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_tasks_backend ON provider_tasks(backend)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_tasks_status ON provider_tasks(status)")
    conn.commit()


def _provider_task_db_file() -> Path:
    configured = os.environ.get("A2A_PROVIDER_TASK_DB_FILE") or os.environ.get("MAOS_RUNTIME_DB_FILE")
    if configured:
        return Path(configured)
    base = Path(os.environ.get("MAOS_DATA_DIR", "D:/dev/MAOS/temporal-data"))
    return base / "maos_runtime.db"


def _invocation_registry_file() -> str:
    configured = os.environ.get("A2A_INVOCATION_REGISTRY_FILE")
    if configured:
        return configured
    base = Path(os.environ.get("MAOS_DATA_DIR", "D:/dev/MAOS/temporal-data"))
    return str(base / "a2a-invocations.json")


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
