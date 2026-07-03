# Provider Runtime Lifecycle

`ProviderRuntime` is the common lifecycle template for Agent backends. Workflow
activities call the stable provider API (`create`, `poll`, `cancel`, `resume`,
`events`, `artifacts`) while each provider supplies only backend-specific driver
hooks.

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

Providers can opt into the unified lifecycle by setting:

```python
uses_lifecycle_driver = True
```

Then implement:

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

Providers that have not opted in continue to use the legacy `_create()` and
`_poll()` methods.

## Current Migration Status

Migrated to lifecycle hooks:

- `SimulatorProvider`
- `EvaluatorProvider`
- `CodexCliProvider`
- `ClaudeCliProvider`
- `ClaudeHuaweiCliProvider`
- `MulticaProvider`
- `HermesOneshotProvider`

Still using legacy methods internally:

- None of the built-in providers.

The staged migration kept each backend's existing transport implementation
stable while moving provider-level create/poll orchestration into the common
runtime template.

## Next Migration Step

The next useful step is to harden the shared remote-provider driver with common
HTTP error normalization, retry classification, and cancel/resume projection
helpers so `Multica` and `Hermes` share more transport-level behavior without
duplicating backend-specific API details.
