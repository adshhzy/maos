"""Local deterministic execution sandbox for benchmark checks."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self) | {"passed": self.passed}


def make_eval_workspace(prefix: str = "maos-eval-") -> Path:
    root = Path(os.environ.get("MAOS_EVALUATION_TMPDIR", tempfile.gettempdir()))
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(root)))


def write_files(directory: Path, files: dict[str, str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        target = (directory / name).resolve()
        if directory.resolve() not in target.parents and target != directory.resolve():
            raise ValueError(f"Unsafe evaluation output path: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def run_command(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float = 60,
) -> CommandResult:
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        exit_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        stderr = f"{stderr}\nCommand timed out after {timeout_seconds} seconds".strip()
    return CommandResult(
        name=name,
        command=command,
        exit_code=exit_code,
        stdout=_limit_text(stdout),
        stderr=_limit_text(stderr),
        duration_seconds=round(time.time() - started, 3),
    )


def python_command(*args: str) -> list[str]:
    return [sys.executable, *args]


def _limit_text(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...<truncated>"
