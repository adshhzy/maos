import argparse
import signal
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maos_runtime.a2a.codex_pool import start_codex_runtime_pool
from maos_runtime.sandbox_runtime import (
    DEFAULT_TEMPORAL_DB_FILE,
    DEFAULT_TEMPORAL_HOST,
    DEFAULT_TEMPORAL_PORT,
    DEFAULT_TEMPORAL_UI_PORT,
    ControlFlowTaskService,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Temporal worker for the persistent execution sandbox"
    )
    parser.add_argument(
        "--temporal-address",
        default=None,
        help=(
            "Connect to an existing Temporal server, e.g. 127.0.0.1:7233. "
            "If omitted, an embedded persistent dev server is started."
        ),
    )
    parser.add_argument("--temporal-namespace", default="default")
    parser.add_argument("--temporal-host", default=DEFAULT_TEMPORAL_HOST)
    parser.add_argument("--temporal-port", type=int, default=DEFAULT_TEMPORAL_PORT)
    parser.add_argument("--temporal-ui-port", type=int, default=DEFAULT_TEMPORAL_UI_PORT)
    parser.add_argument(
        "--temporal-db-file",
        default=str(DEFAULT_TEMPORAL_DB_FILE),
        help="SQLite DB file for the embedded persistent Temporal dev server.",
    )
    parser.add_argument("--no-temporal-ui", action="store_true")
    args = parser.parse_args()

    codex_pool_status = start_codex_runtime_pool()

    auto_external_address = None
    if args.temporal_address is None and _is_port_open(args.temporal_host, args.temporal_port):
        auto_external_address = f"{args.temporal_host}:{args.temporal_port}"

    service = ControlFlowTaskService(
        temporal_address=args.temporal_address or auto_external_address,
        temporal_namespace=args.temporal_namespace,
        temporal_host=args.temporal_host,
        temporal_port=args.temporal_port,
        temporal_ui=not args.no_temporal_ui,
        temporal_ui_port=args.temporal_ui_port,
        temporal_db_file=args.temporal_db_file,
        start_worker=True,
    )
    stop_event = _install_stop_event()

    print("Temporal worker service started.")
    print(
        "Codex provider pool: "
        f"{codex_pool_status.get('state')} ({codex_pool_status.get('mode')}); "
        f"status={codex_pool_status.get('statusFile')}"
    )
    if args.temporal_address or auto_external_address:
        print(f"Temporal server: external {args.temporal_address or auto_external_address}")
        if auto_external_address and not args.temporal_address:
            print("Detected an existing Temporal listener; embedded startup skipped.")
    else:
        print(
            "Temporal server: embedded persistent dev server at "
            f"{args.temporal_host}:{args.temporal_port}"
        )
        print(f"Temporal DB file: {args.temporal_db_file}")
        if not args.no_temporal_ui:
            print(f"Temporal UI: http://{args.temporal_host}:{args.temporal_ui_port}")
    print("Press Ctrl+C to stop.")
    stop_event.wait()
    _ = service


def _is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _install_stop_event() -> threading.Event:
    event = threading.Event()

    def _set_stop_event(_signum: int, _frame: object) -> None:
        event.set()

    signal.signal(signal.SIGINT, _set_stop_event)
    signal.signal(signal.SIGTERM, _set_stop_event)
    return event


if __name__ == "__main__":
    main()
