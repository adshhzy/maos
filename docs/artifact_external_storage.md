# Artifact External Storage and A2A Dependency Transfer

MAOS can pass upstream node outputs to downstream Agent nodes in two modes:

- `ref`: store the full artifact externally and pass only an artifact reference
  through the A2A message.
- `inline`: pass compact business content directly through the A2A message.

The current default is `ref`.

## Storage Location

The local implementation stores artifacts under:

```text
%MAOS_DATA_DIR%/artifacts
```

If `MAOS_DATA_DIR` is not set, the default base directory is:

```text
D:\dev\MAOS\temporal-data\artifacts
```

Artifacts are content-addressed and deduplicated by SHA-256. The local storage
implementation is intentionally small; it can later be replaced by S3, MinIO,
NAS, or a database-backed store without changing the A2A dependency format.

## Sandbox Artifact API

Sandbox API exposes artifact reads:

```http
GET /api/artifacts/{artifact_ref}
GET /api/artifacts/{artifact_ref}/content
```

`GET /api/artifacts/{artifact_ref}` returns metadata only.

`GET /api/artifacts/{artifact_ref}/content` returns metadata and the complete
business content:

```json
{
  "ok": true,
  "artifact_ref": "sha256-...",
  "content_hash": "sha256:...",
  "mime_type": "application/json",
  "size": 12345,
  "content": "..."
}
```

## A2A Reference Payload

In `ref` mode, downstream A2A messages carry only a reference:

```json
{
  "payload": {
    "artifact_ref": "sha256-...",
    "uri": "http://127.0.0.1:8766/api/artifacts/sha256-.../content",
    "content_hash": "sha256:...",
    "mime_type": "application/json",
    "size": 12345,
    "summary": "Upstream result summary"
  }
}
```

Downstream runtimes that have HTTP access can fetch the full artifact from the
`uri`. Runtimes without HTTP access should use `inline` mode or configure their
provider to resolve artifact refs before constructing the prompt.

## Transfer Mode Switches

Environment-level switch:

```text
A2A_DEPENDENCY_ARTIFACT_MODE=ref
A2A_DEPENDENCY_ARTIFACT_MODE=inline
```

Node or agent-level switch:

```json
{
  "agent": {
    "backend": "claude",
    "artifact_transfer_mode": "inline"
  }
}
```

Allowed values:

- `ref`: A2A carries artifact references; downstream Agent can fetch full
  content through the artifact API.
- `inline`: A2A carries compact business content directly; useful for Agent
  Services that cannot access the artifact API.

## Design Boundary

Artifact external storage is for business outputs that downstream nodes need for
collaboration. It does not store full traces, run logs, comments, or workflow
history. Those runtime details belong to provider task stores, local runtime
logs, and Execution Store projections.

See `docs/api_reference.md` for the complete API map.
