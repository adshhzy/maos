from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start the MAOS Multica Agent Service facade."
    )
    parser.add_argument(
        "--host",
        default=os.getenv("AGENT_SERVICE_HOST", "127.0.0.1"),
        help="Host interface to bind. Defaults to AGENT_SERVICE_HOST or 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("AGENT_SERVICE_PORT", "8091")),
        help="Port to bind. Defaults to AGENT_SERVICE_PORT or 8091.",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload for local development.",
    )
    args = parser.parse_args()

    uvicorn.run(
        "services.agent_service.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
