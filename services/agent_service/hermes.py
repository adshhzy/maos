from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


class HermesError(RuntimeError):
    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


@dataclass(frozen=True)
class HermesClient:
    settings: Settings

    def oneshot(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
        workdir: str | None = None,
    ) -> str:
        effective_timeout = timeout_seconds or self.settings.hermes_timeout_seconds
        effective_workdir = workdir or self.settings.hermes_workdir
        command = [
            _resolve_hermes_executable(self.settings.hermes_bin),
            "--ignore-user-config",
            "--ignore-rules",
            "-z",
            prompt,
        ]
        env = os.environ.copy()
        if self.settings.hermes_git_bash_path:
            env.setdefault("HERMES_GIT_BASH_PATH", self.settings.hermes_git_bash_path)
        try:
            completed = subprocess.run(
                command,
                cwd=effective_workdir,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise HermesError(f"Hermes binary not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise HermesError(
                f"Hermes command timed out after {effective_timeout}s"
            ) from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise HermesError(detail or "Hermes command failed", returncode=completed.returncode)

        return completed.stdout.strip()


def _resolve_hermes_executable(configured_bin: str) -> str:
    """Avoid Windows .cmd wrappers because they corrupt quoted JSON prompt text."""
    path = Path(configured_bin)
    if path.suffix.lower() not in {".cmd", ".bat"}:
        return str(path)

    candidate = path.with_name(".venv") / "Scripts" / "hermes.exe"
    if candidate.exists():
        return str(candidate)

    return str(path)
