"""
Unit tests for HMUX Dashboard, Status Table, and Session Filtering
"""

import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from hmux.state import FleetSession, StateManager
from hmux.dashboard import (
    filter_and_deduplicate_sessions,
    format_uptime,
    format_model_name,
    format_harness_badge,
    build_fleet_table,
    build_external_table,
)


class TestDashboardStatus(unittest.TestCase):

    def test_format_uptime(self):
        now = datetime.now(timezone.utc)

        # 30 seconds ago
        t_30s = (now - timedelta(seconds=30)).isoformat()
        self.assertEqual(format_uptime(t_30s), "30s")

        # 5 minutes ago
        t_5m = (now - timedelta(minutes=5)).isoformat()
        self.assertEqual(format_uptime(t_5m), "5m")

        # 2 hours 15 minutes ago
        t_2h = (now - timedelta(hours=2, minutes=15)).isoformat()
        self.assertEqual(format_uptime(t_2h), "2h 15m")

        # 3 days ago
        t_3d = (now - timedelta(days=3)).isoformat()
        self.assertEqual(format_uptime(t_3d), "3d")

        # None / invalid
        self.assertEqual(format_uptime(None), "-")
        self.assertEqual(format_uptime("invalid-time"), "-")

    def test_format_model_name(self):
        self.assertEqual(format_model_name("reflection/beam-1.0-501b-a23b:free"), "beam-1.0-501b-a23b:free")
        self.assertEqual(format_model_name("opencode/deepseek-v4.1-flash-free"), "deepseek-v4.1-flash-free")
        self.assertEqual(format_model_name("anthropic/claude-3-5-sonnet"), "claude-3-5-sonnet")
        self.assertEqual(format_model_name("openai/gpt-4o"), "gpt-4o")
        self.assertEqual(format_model_name(""), "-")
        self.assertEqual(format_model_name(None), "-")

    def test_format_harness_badge(self):
        self.assertIn("Hermes", format_harness_badge("hermes").plain)
        self.assertIn("OpenCode", format_harness_badge("opencode").plain)
        self.assertIn("Antigravity", format_harness_badge("antigravity").plain)

    def test_filter_and_deduplicate_sessions(self):
        s_managed = FleetSession(
            session_id="hermes-123456",
            harness="hermes",
            task_name="active-task",
            prompt="Hello",
            worktree_path="/tmp",
            pid=99999,
            status="RUNNING",
            metadata={"native_session_id": "local-native-abc"}
        )

        # Redundant raw native session entry for the same underlying session
        s_redundant = FleetSession(
            session_id="local-native-abc",
            harness="hermes",
            task_name="raw-task",
            prompt="",
            worktree_path="/tmp",
            status="COMPLETED",
            metadata={"native_session_id": "local-native-abc"}
        )

        # Separate completed session
        s_completed = FleetSession(
            session_id="codex-654321",
            harness="codex",
            task_name="old-task",
            prompt="",
            worktree_path="/tmp",
            status="COMPLETED"
        )

        # Mock is_alive to return True for s_managed, False for others
        with patch.object(FleetSession, "is_alive", side_effect=lambda: True):
            # Default active filter (show_all=False)
            active = filter_and_deduplicate_sessions([s_managed, s_redundant, s_completed], show_all=False)
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].session_id, "hermes-123456")

            # When show_all=True: includes completed sessions, but deduplicates the native duplicate
            all_deduped = filter_and_deduplicate_sessions([s_managed, s_redundant, s_completed], show_all=True)
            self.assertEqual(len(all_deduped), 2)
            ids = [s.session_id for s in all_deduped]
            self.assertIn("hermes-123456", ids)
            self.assertIn("codex-654321", ids)
            self.assertNotIn("local-native-abc", ids)

    def test_build_fleet_table_compact_and_wide(self):
        s = FleetSession(
            session_id="hermes-test",
            harness="hermes",
            task_name="test-task",
            prompt="Test",
            worktree_path="/tmp",
            status="RUNNING"
        )
        # Compact (< 100 cols)
        table_compact = build_fleet_table([s], width=80)
        self.assertEqual(len(table_compact.columns), 4)

        # Wide (>= 100 cols)
        table_wide = build_fleet_table([s], width=120)
        self.assertEqual(len(table_wide.columns), 7)

    @patch("psutil.Process")
    def test_pid_recycling_guard(self, mock_proc_cls):
        mock_proc = MagicMock()
        mock_proc.is_running.return_value = True
        mock_proc.status.return_value = "sleeping"
        # The recycled process belongs to a completely different binary
        mock_proc.name.return_value = "unrelated_daemon"
        mock_proc.cmdline.return_value = ["/usr/bin/unrelated_daemon", "--foreground"]
        mock_proc_cls.return_value = mock_proc

        session = FleetSession(
            session_id="hermes-dead-pid",
            harness="hermes",
            task_name="task",
            prompt="",
            worktree_path="/tmp",
            pid=5555,
            status="RUNNING"
        )
        # Should detect that PID 5555 was recycled and does not belong to hermes
        self.assertFalse(session.is_alive())

    def test_build_mesh_activity_table(self):
        from hmux.dashboard import build_mesh_activity_table
        table = build_mesh_activity_table(limit=5)
        self.assertIn("Mesh", table.title)
        self.assertEqual(len(table.columns), 4)


if __name__ == "__main__":
    unittest.main()
