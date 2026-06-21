import argparse
import os
from pathlib import Path

from maos_runtime.sandbox_runtime import (
    DEFAULT_TEMPORAL_DB_FILE,
    DEFAULT_TEMPORAL_HOST,
    DEFAULT_TEMPORAL_PORT,
    DEFAULT_TEMPORAL_UI_PORT,
    ControlFlowTaskService,
)
from simulator.simulator_service import start_simulator_server
from web.web_visualize import start_web_server


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_SIMULATOR_HOST = "127.0.0.1"
DEFAULT_SIMULATOR_PORT = 8767


def _truthy_env(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.lower() in {"1", "true", "yes", "on"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the persistent execution sandbox service"
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--simulator-host", default=DEFAULT_SIMULATOR_HOST)
    parser.add_argument("--simulator-port", type=int, default=DEFAULT_SIMULATOR_PORT)
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
    parser.add_argument(
        "--no-worker",
        action="store_true",
        help=(
            "Run only execution-api and connect to Temporal without starting "
            "the embedded execution-worker. Start execution_worker.py separately."
        ),
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help=(
            "Enable production guardrails. Requires --temporal-address so the "
            "embedded Temporal dev server is not started accidentally."
        ),
    )
    args = parser.parse_args()
    production = args.production or _truthy_env("PRODUCTION_MODE")
    if production and not args.temporal_address:
        parser.error("--production requires --temporal-address")

    simulator_url = f"http://{args.simulator_host}:{args.simulator_port}"
    sandbox_url = f"http://{args.host}:{args.port}"
    os.environ["SIMULATOR_API_BASE"] = simulator_url
    os.environ["SANDBOX_API_BASE"] = sandbox_url

    simulator_server = start_simulator_server(
        host=args.simulator_host,
        port=args.simulator_port,
    )
    manager = ControlFlowTaskService(
        temporal_address=args.temporal_address,
        temporal_namespace=args.temporal_namespace,
        temporal_host=args.temporal_host,
        temporal_port=args.temporal_port,
        temporal_ui=not args.no_temporal_ui,
        temporal_ui_port=args.temporal_ui_port,
        temporal_db_file=args.temporal_db_file,
        start_worker=not args.no_worker,
    )
    web_server = start_web_server(
        host=args.host,
        port=args.port,
        manager=manager,
        examples_dir=Path("examples").resolve(),
        simulator_url=simulator_url,
    )

    print(f"Persistent execution sandbox running at {sandbox_url}")
    if production:
        print("production mode: enabled")
    if args.temporal_address:
        print(f"Temporal server: external {args.temporal_address}")
    else:
        print(
            "Temporal server: embedded persistent dev server at "
            f"{args.temporal_host}:{args.temporal_port}"
        )
        print(f"Temporal DB file: {args.temporal_db_file}")
        if not args.no_temporal_ui:
            print(f"Temporal UI: http://{args.temporal_host}:{args.temporal_ui_port}")
    print(
        "Simulator microservice running at "
        f"{simulator_url}"
    )
    if args.no_worker:
        print("execution-worker: external process expected")
    else:
        print("execution-worker: embedded in execution-api process")
    print("Press Ctrl+C to stop.")
    try:
        web_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        simulator_server.server_close()
        web_server.server_close()


if __name__ == "__main__":
    main()
