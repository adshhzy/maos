"""Codex runtime warm-up and process-pool state.

当前 Codex CLI 的稳定执行入口仍是 `codex exec`。这个模块负责在 worker 启动时
后台预热一次 CLI，并把健康状态持久化，避免业务节点第一次调用时才发现认证或网络问题。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from maos_runtime.runtime_config import (
    codex_app_server_url,
    codex_cli_bin,
    codex_runtime_pool_mode,
    codex_sandbox_mode,
    codex_warmup_enabled,
    codex_warmup_prompt,
    codex_warmup_timeout_seconds,
    codex_warmup_wait,
    codex_workdir,
)


_POOL_LOCK = threading.Lock()
_WARMUP_THREAD: threading.Thread | None = None
_APP_SERVER_PROCESS: subprocess.Popen[Any] | None = None
_STATUS: dict[str, Any] = {
    "state": "not_started",
    "mode": codex_runtime_pool_mode(),
    "message": "Codex runtime pool has not been started.",
}


def start_codex_runtime_pool() -> dict[str, Any]:
    """Start optional Codex warm-up/pool work in the background."""

    if not codex_warmup_enabled():
        _set_status(
            {
                "state": "disabled",
                "mode": codex_runtime_pool_mode(),
                "message": "Codex provider warm-up is disabled.",
            }
        )
        return codex_runtime_pool_status()

    mode = codex_runtime_pool_mode()
    if mode == "app-server":
        _start_app_server_if_needed()

    global _WARMUP_THREAD
    with _POOL_LOCK:
        if _WARMUP_THREAD and _WARMUP_THREAD.is_alive():
            return dict(_STATUS)
        if _STATUS.get("state") in {"ready", "warming"}:
            return dict(_STATUS)
        _set_status_locked(
            {
                "state": "warming",
                "mode": mode,
                "message": "Codex CLI warm-up is running in background.",
                "startedAt": _now(),
            }
        )
        _WARMUP_THREAD = threading.Thread(target=_run_warmup, name="codex-provider-warmup", daemon=True)
        _WARMUP_THREAD.start()
        return dict(_STATUS)


def ensure_codex_runtime_pool_started() -> dict[str, Any]:
    status = start_codex_runtime_pool()
    if codex_warmup_wait() and status.get("state") == "warming":
        return wait_for_codex_warmup(codex_warmup_timeout_seconds())
    return status


def wait_for_codex_warmup(timeout_seconds: float | None = None) -> dict[str, Any]:
    deadline = time.time() + float(timeout_seconds or codex_warmup_timeout_seconds())
    while time.time() < deadline:
        status = codex_runtime_pool_status()
        if status.get("state") in {"ready", "failed", "disabled"}:
            return status
        time.sleep(0.5)
    status = codex_runtime_pool_status()
    if status.get("state") == "warming":
        _set_status(
            {
                **status,
                "state": "timeout",
                "message": "Codex CLI warm-up did not finish before the configured timeout.",
                "finishedAt": _now(),
            }
        )
    return codex_runtime_pool_status()


def codex_runtime_pool_status() -> dict[str, Any]:
    with _POOL_LOCK:
        status = dict(_STATUS)
    if status.get("state") in {"not_started", "disabled"}:
        persisted = _read_status_file()
        if persisted:
            status = persisted
    status["statusFile"] = str(_status_file())
    return status


def stop_codex_runtime_pool() -> None:
    global _APP_SERVER_PROCESS
    with _POOL_LOCK:
        proc = _APP_SERVER_PROCESS
        _APP_SERVER_PROCESS = None
    if proc and proc.poll() is None:
        proc.terminate()


def _run_warmup() -> None:
    run_dir = _pool_dir() / "warmup"
    run_dir.mkdir(parents=True, exist_ok=True)
    output_file = run_dir / "final_output.md"
    stdout_file = run_dir / "stdout.jsonl"
    stderr_file = run_dir / "stderr.log"
    prompt = codex_warmup_prompt()
    command = _warmup_command(output_file)
    started_at = time.time()

    with stdout_file.open("w", encoding="utf-8", errors="replace") as stdout_handle, stderr_file.open(
        "w", encoding="utf-8", errors="replace"
    ) as stderr_handle:
        try:
            proc = subprocess.Popen(
                subprocess.list2cmdline(command) if _needs_shell(command[0]) else command,
                cwd=str(_workdir()),
                stdin=subprocess.PIPE,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=_needs_shell(command[0]),
            )
            assert proc.stdin is not None
            proc.stdin.write(prompt)
            proc.stdin.close()
            return_code = proc.wait(timeout=codex_warmup_timeout_seconds())
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc.pid if "proc" in locals() else 0)
            _set_status(
                {
                    "state": "failed",
                    "mode": codex_runtime_pool_mode(),
                    "message": "Codex CLI warm-up timed out.",
                    "elapsedSeconds": round(time.time() - started_at, 2),
                    "command": _display_command(command),
                    "stdoutFile": str(stdout_file),
                    "stderrFile": str(stderr_file),
                    "outputFile": str(output_file),
                    "finishedAt": _now(),
                }
            )
            return
        except Exception as exc:
            _set_status(
                {
                    "state": "failed",
                    "mode": codex_runtime_pool_mode(),
                    "message": f"Codex CLI warm-up failed to start: {exc}",
                    "elapsedSeconds": round(time.time() - started_at, 2),
                    "command": _display_command(command),
                    "stdoutFile": str(stdout_file),
                    "stderrFile": str(stderr_file),
                    "outputFile": str(output_file),
                    "finishedAt": _now(),
                }
            )
            return

    output = _read_text(output_file).strip()
    stderr = _read_text(stderr_file).strip()
    state = "ready" if return_code == 0 and output else "failed"
    message = "Codex CLI warm-up completed." if state == "ready" else "Codex CLI warm-up returned no usable output."
    if return_code != 0:
        message = f"Codex CLI warm-up exited with code {return_code}."
    _set_status(
        {
            "state": state,
            "mode": codex_runtime_pool_mode(),
            "message": message,
            "elapsedSeconds": round(time.time() - started_at, 2),
            "returnCode": return_code,
            "command": _display_command(command),
            "stdoutFile": str(stdout_file),
            "stderrFile": str(stderr_file),
            "outputFile": str(output_file),
            "outputPreview": output[:400],
            "stderrPreview": stderr[:400],
            "finishedAt": _now(),
            "note": (
                "Warm-up validates Codex auth/connectivity. Separate codex exec processes may still "
                "pay their own WebSocket fallback cost; use app-server mode only after local validation."
            ),
        }
    )


def _start_app_server_if_needed() -> None:
    global _APP_SERVER_PROCESS
    with _POOL_LOCK:
        if _APP_SERVER_PROCESS and _APP_SERVER_PROCESS.poll() is None:
            return
    codex_bin = _resolve_codex_bin()
    url = codex_app_server_url()
    command = [codex_bin, "app-server", "--listen", url]
    try:
        proc = subprocess.Popen(
            subprocess.list2cmdline(command) if _needs_shell(command[0]) else command,
            cwd=str(_workdir()),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            shell=_needs_shell(command[0]),
        )
    except Exception as exc:
        _set_status(
            {
                "state": "failed",
                "mode": "app-server",
                "message": f"Codex app-server failed to start: {exc}",
                "appServerUrl": url,
            }
        )
        return
    with _POOL_LOCK:
        _APP_SERVER_PROCESS = proc
        _STATUS["appServerPid"] = proc.pid
        _STATUS["appServerUrl"] = url


def _warmup_command(output_file: Path) -> list[str]:
    command = [_resolve_codex_bin(), "-a", "never", "exec", "--json", "--output-last-message", str(output_file)]
    command.extend(["--cd", str(_workdir()), "--sandbox", codex_sandbox_mode(), "--skip-git-repo-check", "-"])
    return command


def _resolve_codex_bin() -> str:
    configured = codex_cli_bin()
    return shutil.which(configured) or configured


def _workdir() -> Path:
    path = Path(codex_workdir()).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pool_dir() -> Path:
    base = Path(os.environ.get("MAOS_DATA_DIR", r"D:\dev\MAOS\temporal-data"))
    path = base / "codex-provider" / "runtime-pool"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _status_file() -> Path:
    return _pool_dir() / "status.json"


def _set_status(status: dict[str, Any]) -> None:
    with _POOL_LOCK:
        _set_status_locked(status)


def _set_status_locked(status: dict[str, Any]) -> None:
    _STATUS.clear()
    _STATUS.update(status)
    _write_status_file(dict(_STATUS))


def _write_status_file(status: dict[str, Any]) -> None:
    status["statusFile"] = str(_status_file())
    _status_file().write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_status_file() -> dict[str, Any] | None:
    try:
        return json.loads(_status_file().read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _display_command(command: list[str]) -> str:
    display = list(command)
    if display and display[-1] == "-":
        display[-1] = "<stdin>"
    return subprocess.list2cmdline(display)


def _needs_shell(command: str) -> bool:
    return command.lower().endswith((".cmd", ".bat"))


def _kill_process_tree(pid: int) -> None:
    if not pid:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
