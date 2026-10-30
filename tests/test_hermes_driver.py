"""
Unit tests for Hermes Driver & HMUX Router Integration
"""

import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from hmux.hermes import HermesDriver
from hmux.state import FleetSession, StateManager
from hmux.router import MessageRouter
from hmux.registry import HarnessRegistry, HarnessInfo


class TestHermesDriver(unittest.TestCase):

    def setUp(self):
        self.driver = HermesDriver()

    def test_native_session_id_resolution_metadata(self):
        session = FleetSession(
            session_id="hermes-123456",
            harness="hermes",
            task_name="test-task",
            prompt="Hello Hermes",
            worktree_path="/tmp",
            metadata={"native_session_id": "local-abc123456"}
        )
        resolved = self.driver.resolve_native_session_id(session)
        self.assertEqual(resolved, "local-abc123456")

    def test_native_session_id_resolution_from_prefix(self):
        session = FleetSession(
            session_id="local-987654321",
            harness="hermes",
            task_name="test-task",
            prompt="Hello Hermes",
            worktree_path="/tmp"
        )
        resolved = self.driver.resolve_native_session_id(session)
        self.assertEqual(resolved, "local-987654321")

    def test_native_session_id_resolution_from_logfile(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write("Some logs...\nsession_id: local-deadbeef0011223344\nFinished.\n")
            log_path = tf.name

        try:
            session = FleetSession(
                session_id="hermes-logfile-test",
                harness="hermes",
                task_name="test-task",
                prompt="Hello Hermes",
                worktree_path="/tmp",
                log_file=log_path
            )
            resolved = self.driver.resolve_native_session_id(session)
            self.assertEqual(resolved, "local-deadbeef0011223344")
        finally:
            if os.path.exists(log_path):
                os.remove(log_path)

    def test_native_session_id_resolution_from_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.db"
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    role TEXT,
                    content TEXT
                );
            """)
            c.execute("""
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    cwd TEXT,
                    started_at REAL
                );
            """)
            c.execute(
                "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, ?, ?);",
                (1, "local-db-session-001", "user", "Calculate the golden ratio accurately with 10 decimals")
            )
            conn.commit()
            conn.close()

            session = FleetSession(
                session_id="hermes-search-test",
                harness="hermes",
                task_name="test-task",
                prompt="Calculate the golden ratio accurately",
                worktree_path="/tmp"
            )

            with patch.object(HermesDriver, "get_db_path", return_value=db_path):
                resolved = self.driver.resolve_native_session_id(session)
                self.assertEqual(resolved, "local-db-session-001")

    def test_response_extraction_from_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.db"
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    role TEXT,
                    content TEXT
                );
            """)
            c.execute(
                "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, ?, ?);",
                (10, "local-sess-001", "user", "What is 2+2?")
            )
            c.execute(
                "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, ?, ?);",
                (11, "local-sess-001", "assistant", "2 + 2 = 4.")
            )
            conn.commit()
            conn.close()

            with patch.object(HermesDriver, "get_db_path", return_value=db_path):
                resp = self.driver.extract_latest_response("local-sess-001")
                self.assertEqual(resp, "2 + 2 = 4.")

                # If initial_msg_id matches or exceeds latest assistant msg id, returns None
                resp_same = self.driver.extract_latest_response("local-sess-001", initial_msg_id=11)
                self.assertIsNone(resp_same)

    def test_dynamic_session_model_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.db"
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    model TEXT
                );
            """)
            c.execute(
                "INSERT INTO sessions (id, title, model) VALUES (?, ?, ?);",
                ("local-model-sess", "Math Query", "reflection/beam-1.0-501b-a23b:free")
            )
            conn.commit()
            conn.close()

            with patch.object(HermesDriver, "get_db_path", return_value=db_path):
                detected = self.driver.get_session_model("local-model-sess")
                self.assertEqual(detected, "reflection/beam-1.0-501b-a23b:free")

    def test_hermes_command_building(self):
        reg = HarnessRegistry()
        reg._installed["hermes"] = HarnessInfo("hermes", "hermes", "/mock/bin/hermes", "Mock Hermes")

        # Headless command building
        cmd_headless, _ = reg.build_command(
            "hermes",
            "/tmp/worktree",
            prompt="Write a fibonacci function",
            headless=True,
            model="reflection/beam-1.0-501b-a23b:free"
        )
        self.assertIn("chat", cmd_headless)
        self.assertIn("-q", cmd_headless)
        self.assertIn("Write a fibonacci function", cmd_headless)
        self.assertIn("-Q", cmd_headless)
        self.assertIn("--oneshot", cmd_headless)
        self.assertIn("--yolo", cmd_headless)
        self.assertIn("-m", cmd_headless)
        self.assertIn("reflection/beam-1.0-501b-a23b:free", cmd_headless)
        self.assertIn("--in", cmd_headless)
        self.assertIn("/tmp/worktree", cmd_headless)

        # Interactive command building
        cmd_inter, _ = reg.build_command(
            "hermes",
            "/tmp/worktree",
            prompt="Interactive start",
            headless=False,
            model="hermes-3-70b"
        )
        self.assertIn("--in", cmd_inter)
        self.assertIn("/tmp/worktree", cmd_inter)
        self.assertIn("--model", cmd_inter)
        self.assertIn("hermes-3-70b", cmd_inter)
        self.assertIn("-z", cmd_inter)
        self.assertIn("Interactive start", cmd_inter)

    @patch("subprocess.run")
    def test_router_talk_to_hermes(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.stdout = "session_id: local-test-999\nThe golden ratio is approx 1.618."
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        state_mgr = StateManager()
        s_id = "hermes-test-talk"
        session = FleetSession(
            session_id=s_id,
            harness="hermes",
            task_name="talk-test",
            prompt="Initial",
            worktree_path="/tmp",
            metadata={"native_session_id": "local-test-999"},
            status="COMPLETED"
        )
        state_mgr.register(session)

        router = MessageRouter()
        with patch.object(HermesDriver, "extract_latest_response", return_value="The golden ratio is approx 1.618."):
            ok, reply = router.send(
                to_session=s_id,
                message="Tell me about golden ratio",
                from_session="antigravity",
                wait=True,
                timeout=5.0
            )
            self.assertTrue(ok)
            self.assertEqual(reply, "The golden ratio is approx 1.618.")

            # Verify in inbox
            inbox = router.get_inbox(s_id)
            self.assertTrue(any(m["body"] == "Tell me about golden ratio" for m in inbox))
        state_mgr.delete(s_id)

    def test_router_auto_registers_native_hermes_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.db"
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    model TEXT,
                    cwd TEXT
                );
            """)
            c.execute(
                "INSERT INTO sessions (id, title, model, cwd) VALUES (?, ?, ?, ?);",
                ("local-auto-reg-123", "Math Query", "reflection/beam-1.0-501b-a23b:free", "/tmp")
            )
            conn.commit()
            conn.close()

            router = MessageRouter()
            with patch.object(HermesDriver, "get_db_path", return_value=db_path), \
                 patch.object(HermesDriver, "converse", return_value="42 is the answer"):
                ok, reply = router.send(
                    to_session="local-auto-reg-123",
                    message="What is the meaning of life?",
                    from_session="antigravity",
                    wait=True,
                    timeout=5.0
                )
                self.assertTrue(ok)
                self.assertEqual(reply, "42 is the answer")

                registered = StateManager().get("local-auto-reg-123")
                self.assertIsNotNone(registered)
                self.assertEqual(registered.harness, "hermes")
                self.assertEqual(registered.model, "reflection/beam-1.0-501b-a23b:free")
            StateManager().delete("local-auto-reg-123")


if __name__ == "__main__":
    unittest.main()
