"""
Tests for Freebuff PTY Driver and Headless Orchestration in HMUX
"""

import os
import shutil
import unittest
from unittest.mock import patch, MagicMock

from hmux.registry import HarnessRegistry, HarnessInfo
from hmux.supervisor import Supervisor
from hmux.state import StateManager


class TestFreebuffDriverAndHeadless(unittest.TestCase):
    def setUp(self):
        self.registry = HarnessRegistry()
        self.state = StateManager()

    def test_freebuff_official_binary_command_building(self):
        # Both interactive and headless must use the official binary with --cwd
        # and env-based model selection to avoid anti-proxy client suspensions.
        cmd, env = self.registry.build_command(
            harness_name="freebuff",
            worktree_dir="/tmp/test_dir",
            prompt="Analyze codebase",
            headless=True,
            model="MiMo v2.6 Flash Fast"
        )
        self.assertTrue(cmd[0].endswith("freebuff"))
        self.assertIn("--cwd", cmd)
        self.assertIn("/tmp/test_dir", cmd)
        self.assertNotIn("Analyze codebase", cmd)
        self.assertNotIn("--model", cmd)
        self.assertEqual(env.get("FREEBUFF_MODEL"), "MiMo v2.6 Flash Fast")

    @patch("subprocess.Popen")
    def test_native_zero_pty_headless_spawn(self, mock_popen):
        # Native CLI headless harnesses (like codex) execute zero-PTY directly
        mock_proc = MagicMock()
        mock_proc.pid = 88888
        mock_popen.return_value = mock_proc

        sup = Supervisor()
        session = sup.spawn(
            harness_name="codex",
            task_name="test-codex-headless",
            prompt="Write a test",
            headless=True,
            use_worktree=False
        )

        self.assertEqual(session.pid, 88888)
        self.assertTrue(session.metadata.get("headless"))
        self.assertTrue(session.metadata.get("zero_pty"))

        # Verify worker.py was NOT invoked for native headless harness
        called_cmd = mock_popen.call_args[0][0]
        self.assertNotIn("worker.py", str(called_cmd))

        sup.state.delete(session.session_id)

    @patch("subprocess.Popen")
    @patch("threading.Thread")
    def test_freebuff_pty_driver_spawn(self, mock_thread, mock_popen):
        # Freebuff runs inside the managed PTY worker daemon to ensure official
        # UI/ad loop compliance, with automated driver injection
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_popen.return_value = mock_proc

        sup = Supervisor()
        session = sup.spawn(
            harness_name="freebuff",
            task_name="test-freebuff-pty",
            prompt="Execute coding task",
            headless=True,
            use_worktree=False
        )

        self.assertEqual(session.pid, 99999)
        # Verify worker.py WAS invoked so Freebuff runs in its legitimate PTY
        called_cmd = mock_popen.call_args[0][0]
        self.assertIn("worker.py", str(called_cmd))

        # Verify automated driver injection thread was started
        mock_thread.assert_called()

        sup.state.delete(session.session_id)


if __name__ == "__main__":
    unittest.main()
