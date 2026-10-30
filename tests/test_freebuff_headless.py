"""
Tests for Zero-PTY Headless Freebuff integration in HMUX
"""

import os
import shutil
import unittest
from unittest.mock import patch, MagicMock

from hmux.registry import HarnessRegistry, HarnessInfo
from hmux.supervisor import Supervisor
from hmux.state import StateManager


class TestFreebuffHeadless(unittest.TestCase):
    def setUp(self):
        self.registry = HarnessRegistry()
        self.state = StateManager()

    def test_freebuff_headless_command_building(self):
        cmd, env = self.registry.build_command(
            harness_name="freebuff",
            worktree_dir="/tmp/test_dir",
            prompt="Analyze codebase",
            headless=True,
            model="MiMo v2.6 Flash Fast"
        )
        self.assertTrue(cmd[0].endswith("freebuff-headless"))
        self.assertIn("-C", cmd)
        self.assertIn("/tmp/test_dir", cmd)
        self.assertIn("-m", cmd)
        self.assertIn("MiMo v2.6 Flash Fast", cmd)
        self.assertIn("Analyze codebase", cmd)

    def test_freebuff_interactive_command_building(self):
        cmd, env = self.registry.build_command(
            harness_name="freebuff",
            worktree_dir="/tmp/test_dir",
            prompt="Analyze codebase",
            headless=False,
            model="MiMo v2.6 Flash Fast"
        )
        self.assertTrue(cmd[0].endswith("freebuff"))
        self.assertIn("--cwd", cmd)
        self.assertIn("/tmp/test_dir", cmd)
        self.assertNotIn("Analyze codebase", cmd)
        self.assertEqual(env.get("FREEBUFF_MODEL"), "MiMo v2.6 Flash Fast")

    @patch("subprocess.Popen")
    def test_zero_pty_supervisor_spawn(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_popen.return_value = mock_proc

        sup = Supervisor()
        session = sup.spawn(
            harness_name="freebuff",
            task_name="test-zero-pty",
            prompt="Zero PTY prompt",
            headless=True,
            use_worktree=False
        )

        self.assertEqual(session.pid, 99999)
        self.assertTrue(session.metadata.get("headless"))
        self.assertTrue(session.metadata.get("zero_pty"))

        # Verify worker.py was NOT invoked (zero PTY)
        called_cmd = mock_popen.call_args[0][0]
        self.assertNotIn("worker.py", str(called_cmd))
        self.assertTrue(called_cmd[0].endswith("freebuff-headless"))

        # Cleanup test session from state
        sup.state.delete(session.session_id)


if __name__ == "__main__":
    unittest.main()
