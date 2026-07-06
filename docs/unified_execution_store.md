# Unified Execution Store

MAOS now has a first-stage unified persistence projection backed by SQLite.
Temporal is still the durable execution engine, while the Execution Store gives
the Web/API layer a stable place to read workflow, node, provider, artifact, and
evaluation state after Temporal visibility history or provider-local caches are
gone.

## Database

Default path:

```text
%MAOS_DATA_DIR%\execution_store.sqlite3
```

If `MAOS_DATA_DIR` is not set, the default is:

```text
D:\dev\MAOS\temporal-data\execution_store.sqlite3
```

Override with:

```text
MAOS_EXECUTION_DB_FILE=D:\path\to\execution_store.sqlite3
```

## Tables

- `workflow_runs`: one row per workflow/task graph run, including a full task snapshot.
- `graph_definitions`: frozen graph projection used by each run.
- `node_instances`: one row per node visit/attempt; loop iterations do not overwrite prior rows.
- `edge_transitions`: projected control-flow edge decisions and aggregate taken/skipped counts.
- `provider_invocations`: one row per external provider/A2A task invocation.
- `artifacts`: metadata and URI/index rows for artifact files; large content stays in the artifact store.
- `execution_events`: append-style event projection for workflow/provider updates.
- `human_interventions`: human-in-the-loop requests and responses.
- `evaluation_reports`: structured deterministic evaluator summaries.
- `evaluation_test_cases`: per-test status rows for deterministic evaluation candidates.

## Current Write Paths

The implementation is intentionally additive:

- `archive_task()` and `archive_workflow_result()` write complete workflow snapshots to `workflow_runs`, `node_instances`, `edge_transitions`, human intervention, and evaluator tables.
- Sandbox task list/detail reads refresh live Temporal snapshots into `workflow_runs` before returning Web/API data.
- `a2a_task_store` writes provider task changes to `provider_invocations` and provider artifacts.
- `artifact_store` writes artifact metadata rows to `artifacts`.

The existing JSON archive, provider task SQLite, and artifact files remain in place for compatibility.

## Current Read Paths

New Sandbox API endpoints:

```text
GET /api/execution-store/workflows
GET /api/execution-store/workflows/{workflow_id}
```

See `docs/api_reference.md` for the complete Sandbox API map.

`/api/health` also returns the active execution store DB path.

Primary Web/API reads now use the Execution Store:

- `GET /api/tasks` best-effort refreshes live Temporal task rows into the store, then returns a task list read from `workflow_runs`.
- `GET /api/tasks/{task_id}` best-effort refreshes that task into the store, then returns the public `task_detail` projection from the stored snapshot.
- Evaluator output rendering prefers `evaluation_reports` and `evaluation_test_cases`.

Temporal remains the execution engine and signal target. If a snapshot is not
yet present in the Execution Store, the API can still fall back to a live
Temporal manager snapshot for compatibility.

The legacy archive loader can also fall back to the Execution Store if the JSON
archive and provider-store recovery do not have a task snapshot.

## Important Boundaries

- SQLite stores structured state, indexes, metadata, and JSON snapshots.
- Large Agent outputs, traces, code files, and reports should remain as artifacts on disk.
- Provider-specific recovery logic is still present for older records. Future work should collapse that logic into provider-independent store projections.

## Next Steps

1. Add migration management for schema changes.
2. Emit finer-grained `execution_events` from workflow node lifecycle activities.
3. Move Web dashboard panels that need node timelines to direct `node_instances`/`execution_events` queries instead of snapshot JSON.
4. Add compaction/retention policy for old snapshots and artifacts.
