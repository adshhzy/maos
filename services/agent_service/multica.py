from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import Settings


class MulticaError(RuntimeError):
    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


@dataclass(frozen=True)
class MulticaClient:
    settings: Settings

    def _base_args(self, *, include_workspace: bool = True) -> list[str]:
        args = [self.settings.multica_bin]
        if self.settings.multica_profile:
            args.extend(["--profile", self.settings.multica_profile])
        if include_workspace and self.settings.multica_workspace_id:
            args.extend(["--workspace-id", self.settings.multica_workspace_id])
        return args

    def run(
        self,
        args: Iterable[str],
        *,
        include_workspace: bool = True,
        input_text: str | None = None,
    ) -> Any:
        command = self._base_args(include_workspace=include_workspace) + list(args)
        try:
            completed = subprocess.run(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.command_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise MulticaError(f"Multica binary not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise MulticaError(
                f"Multica command timed out after {self.settings.command_timeout_seconds}s"
            ) from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise MulticaError(detail or "Multica command failed", returncode=completed.returncode)

        output = completed.stdout.strip()
        if not output:
            return {}
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {"text": output}

    def daemon_status(self) -> dict[str, Any]:
        return self.run(["daemon", "status", "--output", "json"], include_workspace=False)

    def agents(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        args = ["agent", "list", "--output", "json"]
        if include_archived:
            args.append("--include-archived")
        data = self.run(args)
        return data if isinstance(data, list) else data.get("agents", [])

    def list_tasks(
        self,
        *,
        status: str | None = None,
        assignee_id: str | None = None,
        assignee_name: str | None = None,
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        args = ["issue", "list", "--output", "json", "--limit", str(limit), "--offset", str(offset)]
        if status:
            args.extend(["--status", status])
        if assignee_id:
            args.extend(["--assignee-id", assignee_id])
        if assignee_name:
            args.extend(["--assignee", assignee_name])
        if priority:
            args.extend(["--priority", priority])
        data = self.run(args)
        if isinstance(data, list):
            return {"issues": data, "total": len(data), "has_more": False}
        return data

    def create_task(
        self,
        *,
        title: str,
        description: str,
        agent_id: str | None = None,
        agent_name: str | None = None,
        priority: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
        parent_id: str | None = None,
        allow_duplicate: bool = True,
    ) -> dict[str, Any]:
        args = ["issue", "create", "--output", "json", "--title", title]
        temp_paths: list[str] = []
        if description:
            description_path = self._write_temp_text(description)
            temp_paths.append(description_path)
            args.extend(["--description-file", description_path])
        if agent_id:
            args.extend(["--assignee-id", agent_id])
        if agent_name:
            args.extend(["--assignee", agent_name])
        if priority:
            args.extend(["--priority", priority])
        if status:
            args.extend(["--status", status])
        if project_id:
            args.extend(["--project", project_id])
        if parent_id:
            args.extend(["--parent", parent_id])
        if allow_duplicate:
            args.append("--allow-duplicate")
        try:
            return self.run(args)
        finally:
            self._cleanup_temp_files(temp_paths)

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self.run(["issue", "get", task_id, "--output", "json"])

    def update_task(self, task_id: str, **fields: str | None) -> dict[str, Any]:
        args = ["issue", "update", task_id, "--output", "json"]
        temp_paths: list[str] = []
        mapping = {
            "title": "--title",
            "priority": "--priority",
            "status": "--status",
            "agent_id": "--assignee-id",
            "agent_name": "--assignee",
            "project_id": "--project",
            "parent_id": "--parent",
        }
        for field, flag in mapping.items():
            value = fields.get(field)
            if value is not None:
                args.extend([flag, value])
        description = fields.get("description")
        if description is not None:
            description_path = self._write_temp_text(description)
            temp_paths.append(description_path)
            args.extend(["--description-file", description_path])
        try:
            return self.run(args)
        finally:
            self._cleanup_temp_files(temp_paths)

    def set_task_status(self, task_id: str, status: str) -> Any:
        return self.run(["issue", "status", task_id, status, "--output", "json"])

    def list_comments(
        self,
        task_id: str,
        *,
        recent: int | None = None,
        roots_only: bool = False,
        since: str | None = None,
        summary: bool = False,
    ) -> Any:
        args = ["issue", "comment", "list", task_id, "--output", "json"]
        if recent is not None:
            args.extend(["--recent", str(recent)])
        if roots_only:
            args.append("--roots-only")
        if since:
            args.extend(["--since", since])
        if summary:
            args.append("--summary")
        return self.run(args)

    def add_comment(
        self,
        task_id: str,
        *,
        content: str,
        parent_id: str | None = None,
    ) -> Any:
        content_path = self._write_temp_text(content)
        args = [
            "issue",
            "comment",
            "add",
            task_id,
            "--output",
            "json",
            "--content-file",
            content_path,
        ]
        if parent_id:
            args.extend(["--parent", parent_id])
        try:
            return self.run(args)
        finally:
            self._cleanup_temp_files([content_path])

    def list_metadata(self, task_id: str) -> Any:
        return self.run(["issue", "metadata", "list", task_id, "--output", "json"])

    def set_metadata_value(self, task_id: str, key: str, value: str | int | float | bool) -> Any:
        value_type = "bool" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else "string"
        return self.run(
            [
                "issue",
                "metadata",
                "set",
                task_id,
                "--output",
                "json",
                "--key",
                key,
                "--value",
                str(value).lower() if isinstance(value, bool) else str(value),
                "--type",
                value_type,
            ]
        )

    def runs(self, task_id: str) -> Any:
        return self.run(["issue", "runs", task_id, "--output", "json", "--full-id"])

    def run_messages(
        self,
        run_id: str,
        *,
        issue_id: str | None = None,
        since: int | None = None,
    ) -> Any:
        args = ["issue", "run-messages", run_id, "--output", "json"]
        if issue_id:
            args.extend(["--issue", issue_id])
        if since is not None:
            args.extend(["--since", str(since)])
        return self.run(args)

    @staticmethod
    def _write_temp_text(text: str) -> str:
        temp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".md",
            prefix="agent-service-",
            delete=False,
        )
        with temp:
            temp.write(text)
        return str(Path(temp.name))

    @staticmethod
    def _cleanup_temp_files(paths: list[str]) -> None:
        for path in paths:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
