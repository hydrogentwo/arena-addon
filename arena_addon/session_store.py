"""Durable, dependency-free store for Arena agent sessions.

Spawned tasks and their current output/status are kept in a JSON file so the
MCP server can survive reloads (jcode auto-reloads MCP servers) without losing
in-flight work.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_store_path() -> str:
    env = os.environ.get("ARENA_ADDON_DIR")
    if env:
        return os.path.join(env, "sessions.json")
    base = os.path.join(os.path.expanduser("~"), ".jcode")
    # Keep addon state out of the way if JCODE home is customized.
    jcode_home = os.environ.get("JCODE_HOME")
    if jcode_home:
        base = jcode_home
    return os.path.join(base, "arena-addon", "sessions.json")


class SessionStore:
    """Thread-safe JSON-backed store of {session_id: record}."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_store_path()
        self._lock = threading.RLock()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                self._data = json.load(fh)
        except (FileNotFoundError, ValueError):
            self._data = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def create(self, driver: str, task: str, uploads: list) -> str:
        sid = uuid.uuid4().hex[:12]
        rec = {
            "id": sid,
            "driver": driver,
            "task": task,
            "uploads": uploads or [],
            "status": "pending",
            "result": None,
            "output": "",
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        with self._lock:
            self._data[sid] = rec
            self._save()
        return sid

    def get(self, sid: str) -> dict | None:
        with self._lock:
            rec = self._data.get(sid)
            return json.loads(json.dumps(rec)) if rec else None

    def update(self, sid: str, **fields) -> dict | None:
        with self._lock:
            rec = self._data.get(sid)
            if rec is None:
                return None
            rec.update(fields)
            rec["updated_at"] = utcnow()
            self._save()
            return json.loads(json.dumps(rec))

    def append_output(self, sid: str, chunk: str) -> None:
        with self._lock:
            rec = self._data.get(sid)
            if rec is None:
                return
            rec["output"] = (rec.get("output") or "") + chunk
            rec["updated_at"] = utcnow()
            self._save()

    def list(self) -> list:
        with self._lock:
            rows = []
            for rec in self._data.values():
                rows.append(
                    {
                        "id": rec.get("id"),
                        "status": rec.get("status"),
                        "task": rec.get("task"),
                        "created_at": rec.get("created_at"),
                    }
                )
            return sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)

    def newest_pending_older_than(self, seconds: float) -> str | None:
        threshold = time.time() - seconds
        with self._lock:
            for rec in self._data.values():
                if rec.get("status") in ("pending", "running"):
                    try:
                        created = datetime.fromisoformat(rec.get("created_at") or "")
                        if created.timestamp() <= threshold:
                            return rec.get("id")
                    except ValueError:
                        continue
        return None