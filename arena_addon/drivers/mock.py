"""Mock Arena driver.

Simulates an arena.ai Agent Mode run without any browser. The mock agent
"works" for a short, configurable duration and then produces a small workspace
of files. This is how the MCP contract is exercised in tests and demos, and it
gives jcode a fully working sub-agent path even when no browser is reachable.
"""

from __future__ import annotations

import os
import threading
import time

from .base import Driver, ArenaError


class MockDriver(Driver):
    name = "mock"

    def __init__(self, work_seconds: float = 3.0, delay_seconds: float = 1.0) -> None:
        self.work_seconds = max(0.0, float(work_seconds))
        self.delay_seconds = max(0.0, float(delay_seconds))
        # session_id -> handle
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    # -- internal ---------------------------------------------------------
    def _new_handle(self, task: str) -> dict:
        return {
            "task": task,
            "status": "pending",
            "output": "",
            "result": None,
            "done_at": None,
        }

    def _run(self, sid: str, handle: dict) -> None:
        def worker() -> None:
            try:
                time.sleep(self.delay_seconds)
                with self._lock:
                    handle["status"] = "running"
                    handle["output"] = "Plan: I am a mock Arena agent.\n"
                # Simulated multi-step work.
                steps = 3
                for i in range(steps):
                    time.sleep(max(0.05, self.work_seconds / steps))
                    with self._lock:
                        handle["output"] += f"step {i + 1}/{steps} done\n"
                time.sleep(0.2)
                result = (
                    "SUMMARY (mock):\n"
                    "The Arena agent completed the task end to end.\n"
                    f"Task requested: {handle['task']}\n"
                    f"Produced 3 workspace files. See workspace download.\n"
                )
                with self._lock:
                    handle["status"] = "completed"
                    handle["result"] = result
                    handle["output"] += "completed\n"
            except Exception as exc:  # pragma: no cover - defensive
                with self._lock:
                    handle["status"] = "failed"
                    handle["result"] = f"mock failure: {exc}"

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    # -- Driver interface -------------------------------------------------
    def spawn(self, task: str, upload_files: list | None = None) -> dict:
        if not task or not task.strip():
            raise ArenaError("task must be a non-empty string")
        sid = f"mock-{int(time.time() * 1000)}"
        handle = self._new_handle(task.strip())
        with self._lock:
            self._jobs[sid] = handle
        self._run(sid, handle)
        return {"session_id": sid}

    def _snapshot(self, handle: dict | None) -> dict:
        if handle is None:
            return {"status": "unknown"}
        return {
            "status": handle["status"],
            "output": handle["output"],
            "result": handle["result"],
        }

    def status(self, session_id: str) -> dict:
        with self._lock:
            return self._snapshot(self._jobs.get(session_id))

    def wait(self, session_id: str, timeout_secs: float) -> dict:
        deadline = time.time() + max(0.0, float(timeout_secs))
        while time.time() < deadline:
            with self._lock:
                handle = self._jobs.get(session_id)
                snap = self._snapshot(handle)
            if snap["status"] in ("completed", "failed", "stopped", "unknown"):
                return snap
            time.sleep(0.3)
        return snap

    def download_workspace(self, session_id: str, dest_dir: str) -> list:
        os.makedirs(dest_dir, exist_ok=True)
        written: list[str] = []
        sample = {
            "RESULT.md": "# Result\n\nThe mock Arena agent finished.\n",
            "notes.txt": "Bridged arena Agent Mode into jcode.\n",
            "plan.json": '{ "status": "ok", "steps": 3 }\n',
        }
        for name, content in sample.items():
            path = os.path.join(dest_dir, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            written.append(path)
        return written

    def list_sessions(self) -> list:
        with self._lock:
            return [
                {"id": sid, "status": h["status"], "task": h["task"]}
                for sid, h in self._jobs.items()
            ]