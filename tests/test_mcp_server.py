"""
Unit tests for HMUX Model Context Protocol (MCP) Server
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hmux.mcp_server import TOOLS, handle_call_tool, export_antigravity_schemas


class TestMCPServer(unittest.TestCase):

    def test_tools_definitions(self):
        tool_names = {t["name"] for t in TOOLS}
        expected = {
            "list_active_harnesses",
            "list_all_sessions",
            "inspect_session",
            "talk_to_harness",
            "broadcast_to_harnesses",
            "read_harness_inbox",
            "get_fleet_status",
            "spawn_harness",
            "handover_to_harness",
            "diff_session",
        }
        self.assertTrue(expected.issubset(tool_names), f"Missing tools: {expected - tool_names}")

    def test_get_fleet_status_call(self):
        res = handle_call_tool({"name": "get_fleet_status", "arguments": {"show_all": False}})
        self.assertIn("content", res)
        self.assertEqual(len(res["content"]), 1)
        data = json.loads(res["content"][0]["text"])
        self.assertIn("active_count", data)
        self.assertIn("installed_harnesses", data)
        self.assertIn("sessions", data)
        self.assertIn("external_processes", data)

    def test_list_active_harnesses_call(self):
        res = handle_call_tool({"name": "list_active_harnesses", "arguments": {}})
        self.assertIn("content", res)
        sessions = json.loads(res["content"][0]["text"])
        self.assertIsInstance(sessions, list)

    def test_diff_session_call(self):
        # Missing argument
        res = handle_call_tool({"name": "diff_session", "arguments": {}})
        self.assertTrue(res.get("isError"))
        # Non-existent session
        res = handle_call_tool({"name": "diff_session", "arguments": {"session_id": "non_existent_12345"}})
        self.assertTrue(res.get("isError"))

    def test_spawn_and_handover_missing_args(self):
        res = handle_call_tool({"name": "spawn_harness", "arguments": {}})
        self.assertTrue(res.get("isError"))
        res = handle_call_tool({"name": "handover_to_harness", "arguments": {}})
        self.assertTrue(res.get("isError"))

    def test_unknown_tool_call(self):
        res = handle_call_tool({"name": "non_existent_tool_xyz", "arguments": {}})
        self.assertTrue(res.get("isError"))

    def test_export_antigravity_schemas(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exported = export_antigravity_schemas(tmpdir)
            self.assertEqual(len(exported), len(TOOLS))
            for path in exported:
                self.assertTrue(os.path.exists(path))
                with open(path, "r", encoding="utf-8") as f:
                    schema = json.load(f)
                self.assertIn("name", schema)
                self.assertIn("description", schema)
                self.assertIn("parameters", schema)


if __name__ == "__main__":
    unittest.main()
