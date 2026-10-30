"""
Unit tests for HMUX Inter-Harness Message Router & Sockets
"""

import os
import sys
import time
import socket
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hmux.router import MessageRouter, format_envelope, format_reply_envelope
from hmux.state import StateManager, FleetSession

class TestMessageRouter(unittest.TestCase):

    def setUp(self):
        self.router = MessageRouter()
        self.state = StateManager()
        # Reset test messages for isolation
        self.router._write_raw({"messages": []})

    def test_envelope_formatting(self):
        env = format_envelope("antigravity-1", "Please review auth.py")
        self.assertIn("antigravity-1", env)
        self.assertIn("Please review auth.py", env)
        self.assertIn("hmux talk antigravity-1", env)

        reply = format_reply_envelope("codex-auth", "Looks good, no race condition found.")
        self.assertIn("codex-auth", reply)
        self.assertIn("Looks good, no race condition found.", reply)

    def test_send_and_reply_flow(self):
        # Register a mock session
        s_id = "test-harness-receiver"
        sock_path = f"/tmp/{s_id}.sock"
        if os.path.exists(sock_path):
            os.unlink(sock_path)

        # Create dummy listening socket
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(1)
        srv.setblocking(False)

        mock_session = FleetSession(
            session_id=s_id,
            harness="codex",
            task_name="test-task",
            prompt="",
            worktree_path="/tmp",
            socket_path=sock_path,
            pid=os.getpid(),
            status="RUNNING"
        )
        self.state.register(mock_session)

        try:
            # 1. Send message from caller to receiver
            ok, msg = self.router.send(
                to_session=s_id,
                message="Hello from Antigravity",
                from_session="caller-antigravity",
                wait=False
            )
            self.assertTrue(ok)

            # Verify listening socket received it
            conn, _ = srv.accept()
            received_bytes = conn.recv(4096)
            conn.close()
            self.assertIn(b"Hello from Antigravity", received_bytes)
            self.assertIn(b"caller-antigravity", received_bytes)

            # 2. Receiver replies back to caller
            reply_ok, reply_status = self.router.send(
                to_session="caller-antigravity",
                message="Report: Task finished successfully.",
                from_session=s_id,
                wait=False
            )
            self.assertTrue(reply_ok)

            # 3. Check inbox contains both messages with correct status
            inbox = self.router.get_inbox(session_id=s_id)
            self.assertGreaterEqual(len(inbox), 1)
            replied_msg = [m for m in inbox if m.get("to_session") == s_id and m.get("status") == "REPLIED"]
            self.assertEqual(len(replied_msg), 1)
            self.assertEqual(replied_msg[0]["reply"], "Report: Task finished successfully.")

        finally:
            srv.close()
            if os.path.exists(sock_path):
                os.unlink(sock_path)
            self.state.delete(s_id)

    def test_broadcast(self):
        res = self.router.broadcast("System notice: maintenance complete", from_session="admin")
        self.assertIn("delivered_count", res)
        self.assertIn("recipients", res)

if __name__ == "__main__":
    unittest.main()
