# Unified Execution Store

MAOS has a unified persistence projection backed by SQLite. Temporal remains
the durable execution engine, while the Execution Store is the primary read
model for Web/API workflow, node, provider, artifact, human intervention, and
evaluation state.

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

- `workflow_runs`: one row per workflow/task graph run. It also keeps an
  optional raw task snapshot for audit/debug fallback.
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

- Submitting a graph writes an initial `workflow_runs`, `graph_definitions`, and
  `node_instances` projection immediately after the Temporal workflow starts.
- `archive_task()` and `archive_workflow_result()` write workflow snapshots to
  `workflow_runs`, then project nodes, edges, human interventions, evaluator
  reports, and test cases into structured tables.
- Sandbox task list/detail reads best-effort refresh live Temporal snapshots
  into the store, then read back through structured Execution Store
  projections.
- `a2a_task_store` writes provider task changes to `provider_invocations` and provider artifacts.
- `artifact_store` writes artifact metadata rows to `artifacts`.

The existing JSON archive, provider task SQLite, raw snapshots, and artifact
files remain in place for compatibility.

## Current Read Paths

New Sandbox API endpoints:

```text
GET /api/execution-store/workflows
GET /api/execution-store/workflows/{workflow_id}
```

See `docs/api_reference.md` for the complete Sandbox API map.

`/api/health` also returns the active execution store DB path.

Primary Web/API reads now use structured Execution Store projections:

- `GET /api/tasks` best-effort refreshes live Temporal task rows into the
  store, then returns task rows rebuilt from `workflow_runs` plus graph/node
  projection tables.
- `GET /api/tasks/{task_id}` best-effort refreshes that task into the store,
  then returns the public `task_detail` projection rebuilt from structured
  tables. Raw `snapshot_json` is no longer the primary detail source.
- `GET /api/human-interventions` and
  `GET /api/tasks/{task_id}/human-interventions` read from
  `human_interventions` after a best-effort live refresh.
- Evaluator output rendering prefers `evaluation_reports` and
  `evaluation_test_cases`.

Temporal remains the execution engine and signal target. If a workflow has not
yet been projected into the Execution Store, the API can still fall back to a
live Temporal manager snapshot for compatibility.

The legacy archive loader can also fall back to structured Execution Store
projections if the JSON archive and provider-store recovery do not have a task.

## Important Boundaries

- SQLite stores structured state, indexes, metadata, and optional raw JSON
  snapshots for audit/debug fallback.
- Large Agent outputs, traces, code files, and reports should remain as artifacts on disk.
- Provider-specific recovery logic is still present for older records. New
  Web/API reads should prefer provider-independent store projections.

## Next Steps

1. Add migration management for schema changes.
2. Emit finer-grained `execution_events` from workflow node lifecycle activities.
3. Move Web dashboard timeline panels to direct `node_instances` /
   `execution_events` queries rather than the composed task projection.
4. Add compaction/retention policy for old snapshots and artifacts.
