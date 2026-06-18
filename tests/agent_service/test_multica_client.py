from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from services.agent_service.config import Settings
from services.agent_service.multica import MulticaClient


class MulticaClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MulticaClient(
            Settings(
                multica_bin="multica",
                multica_profile="desktop",
                multica_workspace_id="workspace-1",
                multica_workspaces_root=None,
                hermes_bin="hermes",
                hermes_workdir=".",
                hermes_git_bash_path=None,
                poll_seconds=1,
                command_timeout_seconds=10,
                hermes_timeout_seconds=10,
            )
        )

    @patch("services.agent_service.multica.subprocess.run")
    def test_base_command_includes_profile_and_workspace(self, run_mock) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = json.dumps([])
        run_mock.return_value.stderr = ""

        self.client.agents()

        command = run_mock.call_args.args[0]
        self.assertEqual(command[:5], ["multica", "--profile", "desktop", "--workspace-id", "workspace-1"])
        self.assertEqual(command[5:], ["agent", "list", "--output", "json"])

    @patch("services.agent_service.multica.subprocess.run")
    def test_daemon_status_omits_workspace(self, run_mock) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = json.dumps({"status": "running"})
        run_mock.return_value.stderr = ""

        self.client.daemon_status()

        command = run_mock.call_args.args[0]
        self.assertEqual(command, ["multica", "--profile", "desktop", "daemon", "status", "--output", "json"])


if __name__ == "__main__":
    unittest.main()
