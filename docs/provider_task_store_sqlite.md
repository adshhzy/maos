# Provider Task Store SQLite

Provider task state is persisted in SQLite at:

```text
D:\dev\MAOS\temporal-data\maos_runtime.db
```

The path can be overridden with:

```text
A2A_PROVIDER_TASK_DB_FILE
MAOS_RUNTIME_DB_FILE
MAOS_DATA_DIR
```

## Tables

`provider_tasks` is the primary table.

| Column | Meaning |
|---|---|
| `provider_task_id` | A2A/provider task id, primary key |
| `idempotency_key` | Temporal node dispatch idempotency key |
| `workflow_id` | Parent Temporal workflow id |
| `node_id` | Task graph node id |
| `backend` | Provider backend, such as `claude`, `simulator`, `multica` |
| `status` | Latest A2A task state |
| `created_at` / `updated_at` / `finished_at` | Provider task timestamps |
| `result_artifact_created` | Whether result artifact has been materialized |
| `task_json` | Full A2A task snapshot |

`provider_events` and `provider_artifacts` are reserved for the next step of
storing normalized event streams and artifact metadata separately.

This database is the durable provider/A2A invocation store. It is used for
idempotency recovery, provider task polling, and runtime-output lookup. The
workflow/task dashboard read model is `execution_store.sqlite3`; provider task
records are also projected there as `provider_invocations` where possible.

## Compatibility

The in-memory `TASKS` dict still exists as a compatibility cache for the current
provider implementations. Reads can hydrate from SQLite when a task is missing
from memory.

`a2a-invocations.json` is still written as a JSON mirror unless disabled with:

```text
A2A_DISABLE_JSON_INVOCATION_MIRROR=1
```

This keeps older recovery paths and local debugging tools working while SQLite
remains the durable provider task store. New Web/API task list and task detail
reads should prefer the unified Execution Store projection.
