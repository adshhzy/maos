"""Compatibility entrypoint for the standalone execution-worker service."""

from services.execution_worker import main


if __name__ == "__main__":
    main()
