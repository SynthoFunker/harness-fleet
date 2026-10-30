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
