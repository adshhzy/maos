"""Compatibility entrypoint for the Multica Agent Service facade.

Recommended command:
    python -m services.agent_service --host 127.0.0.1 --port 8091
"""

from services.agent_service.__main__ import main


if __name__ == "__main__":
    main()
