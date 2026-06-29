# Deterministic Evaluator Provider

`backend: evaluator` is a deterministic local provider for comparing Agent-generated outputs. It does not call an LLM. It extracts generated code, runs fixed hidden checks, computes scores, and returns an A2A result artifact.

## Use Case

The first benchmark is `async_ttl_cache`, used to compare:

- a single Claude coding node output
- a multi-Agent Claude workflow final output

The benchmark expects both outputs to contain the main implementation code block:

- `maos_cache.py`

## Task Graph

Use:

```text
example_cmp/evaluate_async_ttl_cache_single_vs_multi_claude.json
```

Fill these graph input fields before running:

```json
{
  "benchmark_id": "async_ttl_cache",
  "single_task_id": "task-single-agent-coding-async-ttl-cache-claude-...",
  "single_node_id": "single_coding_agent",
  "multi_task_id": "task-multi-agent-coding-async-ttl-cache-claude-...",
  "multi_node_id": "final_integrator"
}
```

Or use selector fields to automatically compare the latest completed matching
runs:

```json
{
  "benchmark_id": "async_ttl_cache",
  "single_selector": {
    "graph_id": "single-agent-coding-async-ttl-cache-claude",
    "node_id": "single_coding_agent",
    "node_status": "completed",
    "backend": "claude",
    "status": "completed"
  },
  "multi_selector": {
    "graph_id": "multi-agent-coding-async-ttl-cache-simple-review-loop-claude",
    "node_id": "final_integrator",
    "node_status": "completed",
    "backend": "claude",
    "status": "completed"
  }
}
```

Explicit `single_task_id` / `multi_task_id` values take precedence. When they
are absent, the evaluator calls `GET /api/tasks`, filters by the selector, sorts
by `updated_at`, `finished_at`, `submitted_at`, then `archived_at`, and selects
the newest completed task for each side.

The evaluator provider will fetch task snapshots through the Sandbox API, then use local runtime output APIs for Claude/Codex nodes when available.

## Provider Behavior

The evaluator runs this fixed pipeline:

1. Load the two candidate final outputs.
2. Extract `maos_cache.py` deterministically from Markdown code blocks.
3. Create isolated temporary directories.
4. Copy hidden tests from `maos_runtime/evaluation/benchmarks/async_ttl_cache/hidden_tests.py`.
5. Run:

```text
python -m py_compile maos_cache.py
python -m pytest hidden_tests.py -q
```

6. Score each candidate with a fixed 100-point rubric.
7. Return a JSON report, the compared task/node IDs, and a Markdown summary in the A2A result artifact.

## Rubric

| Dimension | Points |
|---|---:|
| Required files extracted | 10 |
| `maos_cache.py` compiles | 10 |
| Hidden tests | 70 |
| Design/use documentation markers | 10 |

## Output

The node payload includes:

```json
{
  "status": "completed",
  "agent_backend": "evaluator",
  "benchmark_id": "async_ttl_cache",
  "winner": "multi",
  "single_score": 72.0,
  "multi_score": 91.0,
  "score_delta": 19.0,
  "comparison_sources": {
    "single": {
      "task_id": "task-single-agent-coding-...",
      "node_id": "single_coding_agent",
      "has_direct_output": false
    },
    "multi": {
      "task_id": "task-multi-agent-coding-...",
      "node_id": "final_integrator",
      "has_direct_output": false
    }
  },
  "single": {},
  "multi": {},
  "report": {},
  "latest_comment": "Markdown report"
}
```

The Web UI shows `latest_comment` in the Agent Final Output panel for evaluator nodes.

## Hidden Test Privacy

Hidden tests are never sent to the coding Agents. They are only copied into the evaluator temporary directory after the candidate workflows have already completed. Agent-generated test files are intentionally ignored, so the benchmark measures the submitted main implementation rather than tests written by the same Agent.

The `async_ttl_cache` benchmark currently uses a broader hidden suite covering:

- public API and `CacheInfo` contract
- sync decorator hit/miss behavior, keyword argument keys, TTL, LRU, invalidate, and clear
- direct `AsyncTTLCache.get_or_set` and `AsyncTTLCache.aget_or_set`
- async single-flight deduplication for same-key concurrency
- independent execution for different async keys
- exception propagation, inflight cleanup, and retry after failure

## Requirements

`pytest` is required for the benchmark runner and is included in `requirements.txt`.
