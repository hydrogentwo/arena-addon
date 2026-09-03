"""Driver abstraction for arena.ai Agent Mode.

A Driver owns the mechanics of actually talking to an Arena agent. The MCP
server is transport-agnostic: it only knows the Driver interface below.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ArenaError(Exception):
    """Raised when an Arena interaction fails."""


class Driver(ABC):
    """Minimal interface the MCP server relies on."""

    #: short identifier used in session records and logs
    name = "base"

    @abstractmethod
    def spawn(self, task: str, upload_files: list | None = None) -> dict:
        """Kick off an Arena agent for `task`.

        Returns a dict with at least ``{"session_id": str}``. GUI sessions
        typically need to keep a handle so later polling can find them.
        """

    @abstractmethod
    def status(self, session_id: str) -> dict:
        """Return ``{"status": ..., "output": ..., "result": ...}``.

        status is one of: pending | running | completed | failed | stopped.
        """

    @abstractmethod
    def wait(self, session_id: str, timeout_secs: float) -> dict:
        """Block until completion/failure or timeout.

        Returns the same shape as :meth:`status`.
        """

    @abstractmethod
    def download_workspace(self, session_id: str, dest_dir: str) -> list:
        """Fetch the Arena agent's workspace files into `dest_dir`.

        Returns a list of absolute paths that were written.
        """

    @abstractmethod
    def list_sessions(self) -> list:
        """Return short info about sessions this driver can see.

        Not required to be accurate for GUI-only drivers; the MCP server
        prefers its own durable store for listing.
        """