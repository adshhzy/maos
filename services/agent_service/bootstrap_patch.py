from __future__ import annotations

import json
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import Settings


COMPACT_RUNTIME_PROFILES = {
    "maos_compact",
    "maos_compact_agent",
    "compact",
    "compact_agent",
}

DEFAULT_BLOCKED_SKILL_SLUGS = {
    "planning-with-files",
}

DEFAULT_BLOCKED_SKILL_PREFIXES = ("multica-",)


def should_patch_compact_bootstrap(metadata: dict[str, Any]) -> bool:
    runtime_profile = _metadata_value(metadata, "runtime_profile", "execution_profile")
    comment_policy = _metadata_value(metadata, "comment_history_policy")
    compact_flag = _metadata_value(metadata, "maos_compact_bootstrap")
    maos_task = _truthy(metadata.get("maos_task")) or metadata.get("maos_backend") == "multica"

    if compact_flag in {"1", "true", "yes", "on"}:
        return True
    if runtime_profile in COMPACT_RUNTIME_PROFILES:
        return True
    return bool(maos_task and comment_policy in {"disabled", "off", "none"})


def start_compact_bootstrap_patch(
    task_id: str,
    metadata: dict[str, Any],
    settings: Settings,
    run_ids_supplier: Callable[[str], list[str]] | None = None,
) -> None:
    if not should_patch_compact_bootstrap(metadata):
        return
    thread = threading.Thread(
        target=patch_compact_bootstrap,
        args=(task_id, dict(metadata), settings, run_ids_supplier),
        name=f"maos-bootstrap-patch-{task_id[:8]}",
        daemon=True,
    )
    thread.start()


def patch_compact_bootstrap(
    task_id: str,
    metadata: dict[str, Any],
    settings: Settings,
    run_ids_supplier: Callable[[str], list[str]] | None = None,
    *,
    timeout_seconds: float = 75.0,
) -> bool:
    deadline = time.time() + timeout_seconds
    started_after = time.time() - 3
    patched_agents = False
    trimmed_skills = False
    compacted_manifest = False
    last_error = ""
    last_run_root: Path | None = None

    while time.time() < deadline:
        try:
            for run_root in _candidate_run_roots(
                task_id,
                settings,
                run_ids_supplier,
                started_after,
            ):
                last_run_root = run_root
                workdir = run_root / "workdir"
                agents_path = workdir / "AGENTS.md"
                issue_context_path = workdir / ".agent_context" / "issue_context.md"
                skills_dir = workdir / ".agent_context" / "skills"
                if agents_path.exists() and not patched_agents:
                    _write_compact_agents(agents_path, metadata, _kept_skill_slugs(skills_dir, metadata))
                    _write_compact_issue_context(issue_context_path, metadata)
                    patched_agents = True
                if skills_dir.exists():
                    trimmed_skills = _trim_skills(skills_dir, metadata) or trimmed_skills
                compacted_manifest = (
                    _rewrite_sidecar_manifest(run_root, workdir, metadata) or compacted_manifest
                )
                if patched_agents and skills_dir.exists() and compacted_manifest:
                    _write_patch_marker(
                        run_root,
                        metadata,
                        patched_agents,
                        trimmed_skills,
                        compacted_manifest,
                    )
                    return True
        except Exception as exc:
            last_error = str(exc)[:1000]
        time.sleep(0.05 if not patched_agents else 0.75)

    try:
        if last_run_root is not None:
            _write_patch_marker(
                last_run_root,
                metadata,
                patched_agents,
                trimmed_skills,
                compacted_manifest,
                last_error,
            )
    except Exception:
        pass
    return patched_agents


def _candidate_run_roots(
    task_id: str,
    settings: Settings,
    run_ids_supplier: Callable[[str], list[str]] | None,
    started_after: float,
) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    identifiers = [task_id, *_run_ids(task_id, run_ids_supplier)]
    for identifier in identifiers:
        root = _run_root_for_identifier(identifier, settings)
        if root is None:
            continue
        key = str(root).lower()
        if key not in seen:
            roots.append(root)
            seen.add(key)
    for root in _recent_matching_run_roots(task_id, settings, started_after):
        key = str(root).lower()
        if key not in seen:
            roots.append(root)
            seen.add(key)
    return roots


def _run_ids(
    task_id: str,
    run_ids_supplier: Callable[[str], list[str]] | None,
) -> list[str]:
    if run_ids_supplier is None:
        return []
    try:
        return run_ids_supplier(task_id)
    except Exception:
        return []


def _run_root_for_identifier(identifier: str, settings: Settings) -> Path | None:
    if not identifier:
        return None
    base = _workspaces_base(settings)
    if base is None:
        return None
    prefix = identifier[:8]
    exact = base / prefix
    if exact.exists() or not base.exists():
        return exact
    matches = sorted(base.glob(f"{prefix}*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else exact


def _recent_matching_run_roots(
    task_id: str,
    settings: Settings,
    started_after: float,
) -> list[Path]:
    base = _workspaces_base(settings)
    if base is None or not base.exists():
        return []
    roots: list[Path] = []
    for child in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:40]:
        try:
            if not child.is_dir() or child.stat().st_mtime < started_after:
                continue
            issue_context = child / "workdir" / ".agent_context" / "issue_context.md"
            agents = child / "workdir" / "AGENTS.md"
            if _file_contains(issue_context, task_id) or _file_contains(agents, task_id):
                roots.append(child)
        except Exception:
            continue
    return roots


def _file_contains(path: Path, needle: str) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        return needle in path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False


def _workspaces_base(settings: Settings) -> Path | None:
    if not settings.multica_workspace_id:
        return None
    if settings.multica_workspaces_root:
        root = Path(settings.multica_workspaces_root)
    else:
        profile = settings.multica_profile or "default"
        root = Path.home() / f"multica_workspaces_{profile}"
    return root / settings.multica_workspace_id


def _write_compact_agents(
    agents_path: Path,
    metadata: dict[str, Any],
    skill_slugs: list[str],
) -> None:
    existing = agents_path.read_text(encoding="utf-8", errors="replace")
    identity = _extract_identity(existing)
    issue_id = metadata.get("issue_id") or metadata.get("multica_task_id") or "<issue-id>"
    node_id = metadata.get("node_id") or ""
    workflow_id = metadata.get("workflow_id") or ""
    skills_text = (
        "\n".join(f"- `{slug}`: read `.agent_context/skills/{slug}/SKILL.md` only if useful." for slug in skill_slugs)
        if skill_slugs
        else "- No extra sidecar skills are loaded for this compact MAOS run."
    )
    body = f"""<!-- BEGIN MULTICA-RUNTIME (auto-managed; MAOS compact bootstrap) -->
# MAOS Compact Agent Runtime

You are executing one Agent call inside a MAOS persistent multi-agent DAG.

## Agent Identity

{identity}

## MAOS Runtime Policy

- Treat the issue description as the authoritative task input.
- Do not read issue comments, comment history, or metadata unless the task description explicitly asks for them.
- Do not inspect repositories, workspace files, external documents, or web pages unless the task description explicitly asks for that work.
- Use dependency data only from the original task input and upstream node results embedded in the issue description.
- Keep the result concise and focused on this DAG node.

## Required Loop

1. Run `multica issue get {issue_id} --output json` and read the description.
2. Complete node `{node_id}` for workflow `{workflow_id}` using the description payload.
3. Write the result to a UTF-8 file, then post it with `multica issue comment add {issue_id} --content-file <path> --output json`.
4. Set the issue to `in_review` or `done` when complete. If blocked, post the blocker and set status to `blocked`.

## Skills

{skills_text}

<!-- END MULTICA-RUNTIME -->
"""
    tmp = agents_path.with_suffix(".maos-compact.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(agents_path)


def _write_compact_issue_context(path: Path, metadata: dict[str, Any]) -> None:
    if not path.parent.exists():
        return
    issue_id = metadata.get("issue_id") or metadata.get("multica_task_id") or "<issue-id>"
    node_id = metadata.get("node_id") or ""
    workflow_id = metadata.get("workflow_id") or ""
    body = f"""# MAOS Compact Task Assignment

**Issue ID:** {issue_id}
**Workflow ID:** {workflow_id}
**Node ID:** {node_id}

Run `multica issue get {issue_id} --output json` to fetch the issue description.
Do not read comment history, metadata, repositories, workspace files, or skill files unless the issue description explicitly asks for that.
"""
    tmp = path.with_suffix(".maos-compact.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def _extract_identity(existing: str) -> str:
    match = re.search(r"## Agent Identity\s+(.*?)(?:\n## Available Commands|\n## Comment Formatting|\Z)", existing, re.S)
    if not match:
        return "Use the assigned Multica Agent identity and role instructions."
    identity = match.group(1).strip()
    return identity or "Use the assigned Multica Agent identity and role instructions."


def _trim_skills(skills_dir: Path, metadata: dict[str, Any]) -> bool:
    if not skills_dir.exists():
        return False
    keep = set(_kept_skill_slugs(skills_dir, metadata))
    changed = False
    for child in skills_dir.iterdir():
        if child.is_dir() and child.name not in keep:
            shutil.rmtree(child, ignore_errors=True)
            changed = True
    return changed


def _rewrite_sidecar_manifest(run_root: Path, workdir: Path, metadata: dict[str, Any]) -> bool:
    manifest_path = run_root / ".multica_sidecar_manifest.json"
    if not manifest_path.exists():
        return False

    issue_context = workdir / ".agent_context" / "issue_context.md"
    skills_dir = workdir / ".agent_context" / "skills"
    keep = set(_kept_skill_slugs(skills_dir, metadata))
    files: list[str] = []
    dirs: list[str] = []

    if issue_context.exists():
        files.append(str(issue_context))
        dirs.append(str(issue_context.parent))

    if skills_dir.exists():
        dirs.append(str(skills_dir))
        for slug in sorted(keep):
            skill_root = skills_dir / slug
            if not skill_root.exists() or not skill_root.is_dir():
                continue
            dirs.append(str(skill_root))
            for child in sorted(skill_root.rglob("*")):
                if child.is_dir():
                    dirs.append(str(child))
                elif child.is_file():
                    files.append(str(child))

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(manifest, dict):
            manifest = {}
    except Exception:
        manifest = {}

    manifest["files"] = _dedupe_preserve_order(files)
    manifest["dirs"] = _dedupe_preserve_order(dirs)
    tmp = manifest_path.with_suffix(".maos-compact.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    tmp.replace(manifest_path)
    return True


def _kept_skill_slugs(skills_dir: Path, metadata: dict[str, Any]) -> list[str]:
    explicit = _csv(metadata.get("maos_allowed_skill_slugs") or metadata.get("allowed_skill_slugs"))
    if explicit:
        return sorted(explicit)
    policy = _metadata_value(metadata, "skill_loading_policy")
    if policy in {"none", "disabled", "off", "role_relevant_only", ""}:
        return []
    if not skills_dir.exists():
        return []
    slugs: list[str] = []
    for child in skills_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name in DEFAULT_BLOCKED_SKILL_SLUGS:
            continue
        if any(child.name.startswith(prefix) for prefix in DEFAULT_BLOCKED_SKILL_PREFIXES):
            continue
        slugs.append(child.name)
    return sorted(slugs)


def _write_patch_marker(
    run_root: Path,
    metadata: dict[str, Any],
    patched_agents: bool,
    trimmed_skills: bool,
    compacted_manifest: bool,
    error: str = "",
) -> None:
    marker = {
        "patched_at": datetime.now(timezone.utc).isoformat(),
        "patched_agents": patched_agents,
        "trimmed_skills": trimmed_skills,
        "runtime_profile": metadata.get("runtime_profile"),
        "comment_history_policy": metadata.get("comment_history_policy"),
        "metadata_policy": metadata.get("metadata_policy"),
        "skill_loading_policy": metadata.get("skill_loading_policy"),
        "compacted_manifest": compacted_manifest,
        "error": error,
    }
    (run_root / ".maos_bootstrap_patch.json").write_text(
        json.dumps(marker, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _metadata_value(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if value is not None:
            return str(value).lower().replace("-", "_")
    return ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _csv(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        result.append(value)
        seen.add(key)
    return result
