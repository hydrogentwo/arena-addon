"""arena-addon MCP server.

Exposes arena.ai Agent Mode to jcode as MCP tools. jcode loads this over stdio
and calls the tools as a sub-agent: spawn a task, wait for it, and optionally
pull back the Arena agent's workspace files.

The server is intentionally dependency-free (stdlib only). It speaks the MCP
stdio transport: newline-delimited JSON-RPC 2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "arena-addon", "version": "0.1.0"}

TERMINAL_STATES = ("completed", "failed", "stopped", "timeout", "blocked_request")


class Coordinator:
    """Owns the durable store, a Driver, and per-session poll threads."""

    def __init__(self, driver, store, poll_interval: float = 1.0) -> None:
        self.driver = driver
        self.store = store
        self.poll_interval = max(0.2, float(poll_interval))
        self._monitors: dict[str, threading.Thread] = {}

    # ---- internal -----------------------------------------------------
    def _driver_sid(self, sid: str) -> str:
        rec = self.store.get(sid)
        if not rec:
            raise LookupError(f"no such session: {sid}")
        return rec.get("driver_sid") or sid

    def _monitor(self, sid: str, driver_sid: str) -> None:
        try:
            while True:
                rec = self.store.get(sid)
                if not rec:
                    return
                if rec.get("status") in TERMINAL_STATES:
                    return
                try:
                    snap = self.driver.status(driver_sid)
                except Exception as exc:  # noqa: BLE001
                    self.store.update(sid, status="failed", result=f"{type(exc).__name__}: {exc}")
                    return
                status = snap.get("status", "running")
                result = snap.get("result")
                output = snap.get("output", "")
                self.store.update(sid, status=status, result=result, output=output)
                if status in TERMINAL_STATES:
                    return
                time.sleep(self.poll_interval)
        except Exception:  # noqa: BLE001
            try:
                self.store.update(sid, status="failed", result="monitor crashed")
            except Exception:
                pass

    # ---- tool handlers -------------------------------------------------
    def spawn(self, task: str, upload_files: list | None = None) -> dict:
        sid = self.store.create(driver=self.driver.name, task=task, uploads=upload_files or [])
        try:
            spawned = self.driver.spawn(task, upload_files)
        except Exception as exc:  # noqa: BLE001
            self.store.update(sid, status="failed", result=f"{type(exc).__name__}: {exc}")
            raise
        driver_sid = spawned.get("session_id") or sid
        self.store.update(sid, driver_sid=driver_sid)
        t = threading.Thread(target=self._monitor, args=(sid, driver_sid), daemon=True)
        self._monitors[sid] = t
        t.start()
        rec = self.store.get(sid)
        return {"session_id": sid, "status": rec.get("status"), "driver": self.driver.name}

    def status(self, session_id: str) -> dict:
        rec = self.store.get(session_id)
        if not rec:
            return {"error": f"no such session: {session_id}"}
        return {
            "session_id": session_id,
            "status": rec.get("status"),
            "output": rec.get("output"),
            "result": rec.get("result"),
        }

    def wait(self, session_id: str, timeout_secs: float = 600.0, poll: float | None = None) -> dict:
        rec = self.store.get(session_id)
        if not rec:
            return {"error": f"no such session: {session_id}"}
        interval = poll if poll is not None else self.poll_interval
        deadline = time.time() + max(1.0, float(timeout_secs))
        while time.time() < deadline:
            rec = self.store.get(session_id)
            status = rec.get("status") if rec else None
            if status in TERMINAL_STATES or status is None:
                break
            time.sleep(interval)
        rec = self.store.get(session_id) or {}
        return {
            "session_id": session_id,
            "status": rec.get("status"),
            "output": rec.get("output"),
            "result": rec.get("result"),
        }

    def list(self) -> dict:
        return {"sessions": self.store.list()}

    def download(self, session_id: str, dest_dir: str) -> dict:
        rec = self.store.get(session_id)
        if not rec:
            return {"error": f"no such session: {session_id}"}
        driver_sid = self._driver_sid(session_id)
        os.makedirs(dest_dir, exist_ok=True)
        written = self.driver.download_workspace(driver_sid, dest_dir)
        self.store.update(session_id, workspace=written)
        return {"session_id": session_id, "files": written, "dest_dir": dest_dir}

    # ---- MCP tool descriptors -----------------------------------------
    def tool_specs(self) -> list:
        return [
            {
                "name": "arena_spawn",
                "description": (
                    "Spawn an arena.ai Agent Mode run as a sub-agent of jcode. "
                    "Returns a session_id you can poll with arena_status or block on "
                    "with arena_wait. The Arena agent autonomously plans and completes "
                    "the task in its own workspace."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "Task/instructions for the Arena agent."},
                        "upload_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional absolute file paths to attach (when the UI exposes upload).",
                        },
                    },
                    "required": ["task"],
                },
            },
            {
                "name": "arena_status",
                "description": "Get current status/output/result of a previously spawned Arena agent.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"],
                },
            },
            {
                "name": "arena_wait",
                "description": "Block until the spawned Arena agent finishes (or timeout). Returns final result.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "timeout_secs": {
                            "type": "number",
                            "description": "Max seconds to wait (default 600).",
                        },
                    },
                    "required": ["session_id"],
                },
            },
            {
                "name": "arena_list",
                "description": "List previously spawned Arena agent sessions and their statuses.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "arena_download_workspace",
                "description": "Fetch the Arena agent's workspace files into a local directory (when supported by the driver).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "dest_dir": {"type": "string", "description": "Local directory to write files into."},
                    },
                    "required": ["session_id", "dest_dir"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict) -> dict:
        try:
            if name == "arena_spawn":
                task = str(arguments.get("task", "")).strip()
                if not task:
                    raise ValueError("arena_spawn requires a 'task' string")
                payload = self.spawn(task, arguments.get("upload_files"))
                return _ok(payload)
            if name == "arena_status":
                return _ok(self.status(str(arguments.get("session_id", ""))))
            if name == "arena_wait":
                return _ok(
                    self.wait(
                        str(arguments.get("session_id", "")),
                        float(arguments.get("timeout_secs", 600)),
                    )
                )
            if name == "arena_list":
                return _ok(self.list())
            if name == "arena_download_workspace":
                return _ok(
                    self.download(
                        str(arguments.get("session_id", "")),
                        str(arguments.get("dest_dir", "")),
                    )
                )
            return _err(f"unknown tool: {name}")
        except LookupError as exc:
            return _err(str(exc))
        except Exception as exc:  # noqa: BLE001
            return _err(f"{type(exc).__name__}: {exc}")


def _ok(data) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(data, indent=2, ensure_ascii=False)}],
        "isError": False,
    }


def _err(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


class McpServer:
    """Minimal MCP stdio server. One line of JSON-RPC 2.0 per message."""

    def __init__(self, coordinator: Coordinator) -> None:
        self.coordinator = coordinator

    def _send(self, msg: dict) -> None:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()

    def _handle(self, msg) -> None:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        # Notifications carry no id and get no response.
        if msg_id is None:
            if method == "notifications/initialized":
                pass
            return

        if method == "initialize":
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": SERVER_INFO,
                    },
                }
            )
        elif method == "ping":
            self._send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif method == "tools/list":
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": self.coordinator.tool_specs(), "nextCursor": None},
                }
            )
        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            result = self.coordinator.call_tool(name, arguments)
            self._send({"jsonrpc": "2.0", "id": msg_id, "result": result})
        else:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
            )

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                self._handle(msg)
            except Exception as exc:  # noqa: BLE001
                if "id" in msg:
                    self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": msg.get("id"),
                            "error": {"code": -32603, "message": str(exc)},
                        }
                    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arena-addon", description="bridge arena.ai Agent Mode into jcode")
    parser.add_argument("--driver", default=None, help="driver: mock (default) or cdp")
    parser.add_argument("--mock-work-seconds", type=float, default=None)
    parser.add_argument("--mock-delay-seconds", type=float, default=None)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--store", default=None, help="path to the durable session store JSON")
    args = parser.parse_args(argv)

    from .drivers import create_driver
    from .session_store import SessionStore

    store = SessionStore(args.store)
    kwargs = {}
    if args.mock_work_seconds is not None:
        kwargs["work_seconds"] = args.mock_work_seconds
    if args.mock_delay_seconds is not None:
        kwargs["delay_seconds"] = args.mock_delay_seconds
    driver = create_driver(args.driver, **kwargs)
    coord = Coordinator(driver, store, poll_interval=args.poll_interval)
    server = McpServer(coord)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())