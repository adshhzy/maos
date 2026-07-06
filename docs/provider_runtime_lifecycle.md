# Provider Runtime Lifecycle

`ProviderRuntime` is the common lifecycle template for Agent backends. Workflow
activities call the stable provider API (`create`, `poll`, `cancel`, `resume`,
`events`, `artifacts`) while each provider supplies only backend-specific driver
hooks.

For the full HTTP and internal API map, see `docs/api_reference.md`.

## Lifecycle

The normalized task states are:

```text
TASK_STATE_WORKING
TASK_STATE_INPUT_REQUIRED
TASK_STATE_COMPLETED
TASK_STATE_FAILED
TASK_STATE_CANCELED
```

The runtime treats `COMPLETED`, `FAILED`, and `CANCELED/CANCELLED` as terminal
states.

## Template Hooks

Provider lifecycle hooks are mandatory for every provider:

```python
def start_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
    ...

def inspect_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
    ...
```

`ProviderRuntime.create()` calls `start_invocation()`, persists the returned A2A
task snapshot, and returns `{"task": ...}`.

`ProviderRuntime.poll()` loads the current task hint/store snapshot, calls
`inspect_invocation()`, persists the returned task snapshot, and returns the
standard `{"done": bool, "task": ..., "event": ...}` shape.

`send()` remains a compatibility alias for `create()`, and
`resume_human_response()` remains a compatibility alias for `resume()`. Provider
implementations should not define legacy `_create()` or `_poll()` methods.

## Built-In Providers

All built-in providers directly inherit `ProviderRuntime` and implement the
mandatory lifecycle hooks:

- `SimulatorProvider`
- `EvaluatorProvider`
- `CodexCliProvider`
- `ClaudeCliProvider`
- `ClaudeHuaweiCliProvider`
- `MulticaProvider`
- `HermesOneshotProvider`

Backend transport helpers such as `_send_claude_message()` or
`_poll_multica_task()` remain backend-specific implementation details. The
provider layer is responsible for converting those backend responses into
`ProviderDriverResult`.

## Next Migration Step

The next useful step is to harden the backend transport helpers with common HTTP
error normalization, retry classification, and cancel/resume projection helpers
without reintroducing provider-level create/poll compatibility branches.
