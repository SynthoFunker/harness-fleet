"""
Unit tests for OpenCode Driver & HMUX Router Integration
"""

import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from hmux.opencode import OpenCodeDriver
from hmux.state import FleetSession, StateManager
from hmux.router import MessageRouter


class TestOpenCodeDriver(unittest.TestCase):

    def setUp(self):
        self.driver = OpenCodeDriver()

    def test_native_session_id_resolution(self):
        session = FleetSession(
            session_id="opencode-123456",
            harness="opencode",
            task_name="test-task",
            prompt="Write a quicksort",
            worktree_path="/tmp",
            metadata={"native_session_id": "ses_native_abc"}
        )
        resolved = self.driver.resolve_native_session_id(session)
        self.assertEqual(resolved, "ses_native_abc")

    def test_response_extraction_from_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "opencode.db"
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE session_message (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    type TEXT,
                    time_created INTEGER,
                    data TEXT
                );
            """)
            sample_data = json.dumps({
                "agent": "build",
                "content": [
                    {"type": "text", "text": "Here is the implementation of quicksort in Python."}
                ]
            })
            c.execute(
                "INSERT INTO session_message VALUES (?, ?, ?, ?, ?);",
                ("msg_001", "ses_test", "assistant", 1000, sample_data)
            )
            conn.commit()
            conn.close()

            with patch.object(self.driver, "get_db_path", return_value=db_path):
                resp = self.driver.extract_latest_response("ses_test")
                self.assertEqual(resp, "Here is the implementation of quicksort in Python.")

                # If initial_msg_id matches latest, returns None (no new turn)
                resp_same = self.driver.extract_latest_response("ses_test", initial_msg_id="msg_001")
                self.assertIsNone(resp_same)

    def test_dynamic_session_model_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "opencode.db"
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE session_v2 (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    model TEXT
                );
            """)
            c.execute(
                "INSERT INTO session_v2 VALUES (?, ?, ?);",
                ("ses_dyn_1", "Test", json.dumps({"id": "claude-3-5-sonnet", "providerID": "anthropic"}))
            )
            conn.commit()
            conn.close()

            with patch.object(self.driver, "get_db_path", return_value=db_path):
                detected = self.driver.get_session_model("ses_dyn_1")
                self.assertEqual(detected, "anthropic/claude-3-5-sonnet")

    def test_v2_watermark_ignores_previous_turn(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "opencode.db"
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE session_message (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    type TEXT,
                    seq INTEGER,
                    time_created INTEGER,
                    data TEXT
                );
            """)
            c.executemany(
                "INSERT INTO session_message VALUES (?, ?, ?, ?, ?, ?);",
                [
                    ("m1", "ses_v2", "assistant", 1, 1000,
                     json.dumps({"content": [{"type": "text", "text": "old answer"}]})),
                    ("m2", "ses_v2", "idle", 2, 2000, "{}"),
                    ("m3", "ses_v2", "assistant", 3, 3000,
                     json.dumps({"content": [{"type": "text", "text": "new answer"}]})),
                ],
            )
            conn.commit()
            conn.close()

            with patch.object(self.driver, "get_db_path", return_value=db_path):
                self.assertEqual(self.driver.get_latest_seq("ses_v2"), 3)
                self.assertEqual(
                    self.driver.extract_latest_response("ses_v2", initial_seq=2),
                    "new answer",
                )
                self.assertIsNone(
                    self.driver.extract_latest_response("ses_v2", initial_seq=3)
                )

    def test_v2_session_discovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "opencode.db"
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE session_v2 (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    directory TEXT,
                    model TEXT,
                    time_created INTEGER,
                    time_updated INTEGER,
                    time_busy INTEGER,
                    time_idle INTEGER,
                    parent_id TEXT,
                    time_archived INTEGER,
                    summary_diffs TEXT
                );
            """)
            c.execute(
                "INSERT INTO session_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    "ses_disc_1", "Integrate hmux", "/tmp",
                    json.dumps({"id": "model-x", "providerID": "opencode"}),
                    1000, 2000, 1500, None, None, None, None,
                ),
            )
            conn.commit()
            conn.close()

            with patch.object(self.driver, "get_db_path", return_value=db_path):
                sessions = self.driver.list_sessions()
                self.assertEqual(len(sessions), 1)
                self.assertEqual(sessions[0]["title"], "Integrate hmux")
                self.assertEqual(sessions[0]["model"], "opencode/model-x")
                self.assertTrue(sessions[0]["is_live"])
                info = self.driver.get_session_info("ses_disc_1")
                self.assertEqual(info["directory"], "/tmp")

    @patch("subprocess.run")
    def test_router_talk_to_opencode(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.stdout = "Python is dynamic and expressive."
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        state_mgr = StateManager()
        s_id = "opencode-test-talk"
        session = FleetSession(
            session_id=s_id,
            harness="opencode",
            task_name="talk-test",
            prompt="Initial",
            worktree_path="/tmp",
            metadata={"native_session_id": "ses_mock_999"},
            status="COMPLETED"
        )
        state_mgr.register(session)

        router = MessageRouter()
        with patch.object(OpenCodeDriver, "extract_latest_response", return_value="Rust is great for performance."):
            ok, reply = router.send(
                to_session=s_id,
                message="Tell me about Rust",
                from_session="antigravity",
                wait=True,
                timeout=5.0
            )
            self.assertTrue(ok)
            self.assertEqual(reply, "Rust is great for performance.")

            # Check inbox
            inbox = router.get_inbox(session_id=s_id)
            self.assertTrue(any(m["reply"] == "Rust is great for performance." for m in inbox))

        state_mgr.delete(s_id)


if __name__ == "__main__":
    unittest.main()
