import argparse
from pathlib import Path

from web.web_ui_server import start_web_ui_server


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_SANDBOX_API_BASE = "http://127.0.0.1:8766"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Web UI service for the persistent execution sandbox"
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--sandbox-api-base", default=DEFAULT_SANDBOX_API_BASE)
    parser.add_argument("--static-dir", default=None)
    args = parser.parse_args()

    static_dir = Path(args.static_dir).resolve() if args.static_dir else None
    server = start_web_ui_server(
        host=args.host,
        port=args.port,
        sandbox_api_base=args.sandbox_api_base,
        static_dir=static_dir,
    )

    print(f"Web UI running at http://{args.host}:{args.port}")
    print(f"Sandbox API proxy target: {args.sandbox_api_base}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
