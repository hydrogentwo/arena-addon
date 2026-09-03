"""Tests for the arena-addon MCP server.

Run with:  python3 -m unittest discover -s tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def run_mcp(lines):
    """Run the MCP server over stdio and return {id: response}."""
    payload = "\n".join(json.dumps(m) for m in lines) + "\n"
    env = dict(os.environ)
    env["ARENA_ADDON_DIR"] = tempfile.mkdtemp()
    p = subprocess.run(
        [sys.executable, "-m", "arena_addon", "--driver", "mock",
         "--mock-work-seconds", "0.2", "--mock-delay-seconds", "0.1",
         "--poll-interval", "0.1"],
        input=payload, capture_output=True, text=True, timeout=60, env=env,
        cwd=ROOT,
    )
    out = {}
    for line in p.stdout.splitlines():
        obj = json.loads(line)
        if "id" in obj:
            out[obj["id"]] = obj
    return out


class McpHandshakeTest(unittest.TestCase):
    def test_initialize_and_list(self):
        out = run_mcp([
            {"jsonrpc": "2.0", "id": 0, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        ])
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "arena-addon")
        names = [t["name"] for t in out[1]["result"]["tools"]]
        self.assertEqual(
            names,
            ["arena_spawn", "arena_status", "arena_wait", "arena_list",
             "arena_download_workspace"],
        )

    def test_spawn_returns_session(self):
        out = run_mcp([
            {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "arena_spawn",
                        "arguments": {"task": "hello"}}},
        ])
        text = out[1]["result"]["content"][0]["text"]
        data = json.loads(text)
        self.assertEqual(out[1]["result"]["isError"], False)
        self.assertIn("session_id", data)

    def test_spawn_requires_task(self):
        out = run_mcp([
            {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "arena_spawn", "arguments": {}}},
        ])
        self.assertEqual(out[1]["result"]["isError"], True)

    def test_unknown_tool_is_error(self):
        out = run_mcp([
            {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "arena_nope", "arguments": {}}},
        ])
        self.assertEqual(out[1]["result"]["isError"], True)
        text = out[1]["result"]["content"][0]["text"]
        self.assertIn("unknown tool", text)


class CoordinatorTest(unittest.TestCase):
    def test_full_flow_single_process(self):
        """spawn -> wait(completed) -> download -> list in one process."""
        from arena_addon.server import Coordinator
        from arena_addon.drivers import create_driver
        from arena_addon.session_store import SessionStore

        store = SessionStore(os.path.join(tempfile.mkdtemp(), "sessions.json"))
        driver = create_driver("mock", work_seconds=0.3, delay_seconds=0.1)
        coord = Coordinator(driver, store, poll_interval=0.1)

        r = coord.call_tool("arena_spawn", {"task": "make a game"})
        sid = json.loads(r["content"][0]["text"])["session_id"]

        r = coord.call_tool("arena_wait", {"session_id": sid, "timeout_secs": 20})
        data = json.loads(r["content"][0]["text"])
        self.assertEqual(data["status"], "completed")
        self.assertIn("SUMMARY", data["result"] or "")

        dest = tempfile.mkdtemp()
        r = coord.call_tool("arena_download_workspace", {"session_id": sid, "dest_dir": dest})
        files = json.loads(r["content"][0]["text"])["files"]
        self.assertEqual(len(files), 3)
        self.assertTrue(os.path.isfile(files[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)