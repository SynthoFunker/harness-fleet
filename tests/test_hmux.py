"""
Comprehensive Test Suite for HMUX
"""

import os
import sys
import time
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hmux.registry import HarnessRegistry, HarnessInfo
from hmux.worktree import WorktreeManager
from hmux.state import StateManager, FleetSession
from hmux.handover import HandoverEngine
from hmux.supervisor import Supervisor

class TestHMUX(unittest.TestCase):

    def test_registry_detection(self):
        reg = HarnessRegistry()
        installed = reg.list_installed()
        keys = [h.key for h in installed]
        print(f"\n[Test] Detected installed harnesses: {keys}")
        self.assertIsInstance(installed, list)

        # Verify mock harness registration and case-insensitive lookup
        mock_info = HarnessInfo("mock-agent", "mock-agent", sys.executable, "Mock Agent")
        reg._installed["mock-agent"] = mock_info
        self.assertEqual(reg.get("mock-agent"), mock_info)
        self.assertEqual(reg.get("MOCK-AGENT"), mock_info)

    def test_command_building(self):
        reg = HarnessRegistry()
        # Ensure harnesses exist in registry for test (mocked if not installed on runner)
        if "codex" not in reg._installed:
            reg._installed["codex"] = HarnessInfo("codex", "codex", "/mock/bin/codex", "Mock Codex")
        if "hermes" not in reg._installed:
            reg._installed["hermes"] = HarnessInfo("hermes", "hermes", "/mock/bin/hermes", "Mock Hermes")
        if "pi" not in reg._installed:
            reg._installed["pi"] = HarnessInfo("pi", "pi", "/mock/bin/pi", "Mock Pi")
        if "opencode" not in reg._installed:
            reg._installed["opencode"] = HarnessInfo("opencode", "opencode", "/mock/bin/opencode", "Mock OpenCode")

        # Codex headless with explicit model
        cmd, env = reg.build_command("codex", "/tmp", prompt="Hello Codex", headless=True, model="o3-mini")
        self.assertIn("exec", cmd)
        self.assertIn("-m", cmd)
        self.assertIn("o3-mini", cmd)
        self.assertIn("Hello Codex", cmd)

        # Pi headless with explicit model
        cmd, env = reg.build_command("pi", "/tmp", prompt="Run tests", headless=True, model="claude-3-5-sonnet")
        self.assertIn("-p", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("claude-3-5-sonnet", cmd)
        self.assertIn("Run tests", cmd)

        # OpenCode headless with explicit model
        cmd, env = reg.build_command("opencode", "/tmp", prompt="Fix issue", headless=True, model="gpt-4o")
        self.assertIn("run", cmd)
        self.assertIn("-m", cmd)
        self.assertIn("gpt-4o", cmd)

        # Hermes interactive with model
        cmd, env = reg.build_command("hermes", "/tmp", prompt="Hello Hermes", model="hermes-3-70b")
        self.assertIn("--in", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("hermes-3-70b", cmd)
        self.assertIn("-z", cmd)

        # Freebuff command building: must NOT include positional prompt or --model in CLI args
        if "freebuff" not in reg._installed:
            reg._installed["freebuff"] = HarnessInfo("freebuff", "freebuff", "/usr/local/bin/freebuff", "Mock Freebuff")
        cmd, env = reg.build_command("freebuff", "/tmp", prompt="Hello Freebuff", model="MiMo v2.6 Flash Fast")
        self.assertEqual(cmd, ["/usr/local/bin/freebuff", "--cwd", "/tmp"])
        self.assertNotIn("Hello Freebuff", cmd)
        self.assertNotIn("--model", cmd)
        self.assertEqual(env.get("FREEBUFF_MODEL"), "MiMo v2.6 Flash Fast")

    def test_state_manager(self):
        mgr = StateManager()
        s_id = mgr.generate_id("test")
        session = FleetSession(
            session_id=s_id,
            harness="codex",
            task_name="unit-test",
            prompt="Testing",
            worktree_path="/tmp",
            status="RUNNING"
        )
        mgr.register(session)

        retrieved = mgr.get(s_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.task_name, "unit-test")

        # Test update
        retrieved.status = "COMPLETED"
        mgr.update(retrieved)
        self.assertEqual(mgr.get(s_id).status, "COMPLETED")

        # Clean up
        self.assertTrue(mgr.delete(s_id))
        self.assertIsNone(mgr.get(s_id))

    def test_handover_transmuter_presence(self):
        handover = HandoverEngine()
        self.assertIsInstance(handover.is_available, bool)
        if handover.is_available:
            self.assertIsNotNone(handover.transmuter_bin)
            print(f"[Test] session-transmuter verified at: {handover.transmuter_bin}")
        else:
            print("[Test] session-transmuter not installed on runner; skipping CLI path assertion")

    def test_worktree_sanitization(self):
        wt = WorktreeManager("/tmp")
        safe = wt._sanitize_name("Feature: Auth & Tokens #123!")
        self.assertEqual(safe, "Feature-Auth-Tokens-123")

    def test_supervisor_spawn_and_kill(self):
        sup = Supervisor()
        # Register a portable test runner harness using sys.executable
        sup.registry._installed["test-runner"] = HarnessInfo(
            key="test-runner",
            name="test-runner",
            binary_path=sys.executable,
            description="Test Python Runner"
        )
        session = sup.spawn(
            harness_name="test-runner",
            task_name="test-quick-run",
            prompt="-c",
            extra_args=["import time; time.sleep(10)"],
            use_worktree=False,
            headless=True
        )
        self.assertIsNotNone(session)
        self.assertIsNotNone(session.pid)
        print(f"[Test] Successfully spawned session {session.session_id} (PID: {session.pid})")

        # Wait briefly for process to initialize
        time.sleep(0.5)

        # Verify state
        fetched = sup.state.get(session.session_id)
        self.assertIsNotNone(fetched)

        # Kill and cleanup
        sup.kill(session.session_id)
        sup.state.delete(session.session_id)
        print("[Test] Successfully terminated and cleaned session")

    def test_match_process_excludes_pipewire(self):
        reg = HarnessRegistry()
        # Pipewire and audio daemons must NEVER match pi or any other harness
        self.assertIsNone(reg.match_process("pipewire", ["/usr/bin/pipewire"]))
        self.assertIsNone(reg.match_process("pipewire-pulse", ["/usr/bin/pipewire-pulse"]))
        self.assertIsNone(reg.match_process("wireplumber", ["/usr/bin/wireplumber"]))
        self.assertIsNone(reg.match_process("pulseaudio", ["/usr/bin/pulseaudio"]))

        # Common commands starting with 'pi' must not falsely match 'pi'
        self.assertIsNone(reg.match_process("ping", ["/bin/ping", "127.0.0.1"]))
        self.assertIsNone(reg.match_process("pigz", ["/usr/bin/pigz", "-d", "file.gz"]))
        self.assertIsNone(reg.match_process("pip", ["/usr/bin/pip", "install", "requests"]))
        self.assertIsNone(reg.match_process("pkill", ["/usr/bin/pkill", "something"]))

    def test_match_process_legitimate_pi(self):
        reg = HarnessRegistry()
        # Direct pi execution
        self.assertEqual(reg.match_process("pi", ["/home/user/.pi/agent/bin/pi"]), "pi")
        self.assertEqual(reg.match_process("pi", ["pi", "prompt"]), "pi")

        # Node-wrapped pi execution
        self.assertEqual(
            reg.match_process("node", ["node", "/home/user/.pi/agent/install/releases/1.4.0/node_modules/.bin/pi"]),
            "pi"
        )
        self.assertEqual(
            reg.match_process("node", ["node", "/home/user/.pi/agent/install/releases/1.4.0/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js"]),
            "pi"
        )

        # Pipewire mentioned in user prompt should not prevent matching legitimate harness
        self.assertEqual(reg.match_process("codex", ["codex", "fix pipewire audio issue"]), "codex")
        self.assertEqual(reg.match_process("pi", ["pi", "fix pipewire audio issue"]), "pi")

        # HMUX mentioned in prompt should not exclude harness
        self.assertEqual(reg.match_process("codex", ["codex", "fix hmux issue"]), "codex")

        # HMUX processes themselves must not be matched as harnesses
        self.assertIsNone(reg.match_process("hmux", ["hmux", "status"]))
        self.assertIsNone(reg.match_process("python3", ["python3", "/path/to/hmux_cli.py"]))
        self.assertIsNone(reg.match_process("python3", ["python3", "/path/to/worker.py"]))

    def test_discover_external_harnesses_excludes_pipewire(self):
        from unittest.mock import MagicMock, patch
        reg = HarnessRegistry()
        mgr = StateManager()

        mock_pipewire = MagicMock()
        mock_pipewire.info = {
            'pid': 99991,
            'name': 'pipewire',
            'cmdline': ['/usr/bin/pipewire'],
            'cwd': '/tmp',
            'create_time': 1000000000.0,
            'cpu_percent': 0.1,
            'memory_info': MagicMock(rss=1024 * 1024 * 50)
        }

        mock_pi = MagicMock()
        mock_pi.info = {
            'pid': 99992,
            'name': 'pi',
            'cmdline': ['/usr/local/bin/pi'],
            'cwd': '/tmp',
            'create_time': 1000000000.0,
            'cpu_percent': 0.5,
            'memory_info': MagicMock(rss=1024 * 1024 * 100)
        }

        with patch('psutil.process_iter', return_value=[mock_pipewire, mock_pi]):
            externals = mgr.discover_external_harnesses(reg)
            self.assertEqual(len(externals), 1)
            self.assertEqual(externals[0].harness, "pi")
            self.assertEqual(externals[0].pid, 99992)

    def test_freebuff_driver_parsing(self):
        import tempfile
        import json
        from hmux.freebuff import FreebuffDriver, clean_ansi

        driver = FreebuffDriver()
        raw_text = "\x1b[32m▍Enter a coding task\x1b[0m or / for commands\x1b]0;Title\x07"
        self.assertEqual(clean_ansi(raw_text), "▍Enter a coding task or / for commands")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            msgs_path = tmp_path / "chat-messages.json"
            sample_msgs = [
                {"variant": "user", "content": "Hello Freebuff"},
                {
                    "variant": "ai",
                    "isComplete": True,
                    "blocks": [
                        {"type": "text", "textType": "reasoning", "content": "Internal thought block"},
                        {"type": "text", "textType": "text", "content": "I am Freebuff, ready to code!"}
                    ]
                }
            ]
            msgs_path.write_text(json.dumps(sample_msgs))
            resp = driver.extract_latest_response(tmp_path, min_msg_index=0)
            self.assertEqual(resp, "I am Freebuff, ready to code!")

            # Verify message counting
            self.assertEqual(driver.get_message_count(tmp_path), 2)

if __name__ == "__main__":
    unittest.main()
