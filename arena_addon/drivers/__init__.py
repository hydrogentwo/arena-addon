"""Driver factory for arena.ai agents."""

from __future__ import annotations

import os

from .base import Driver, ArenaError
from .mock import MockDriver


def create_driver(name: str | None = None, **kwargs) -> Driver:
    """Instantiate a driver by name (or the ARENA_DRIVER env var, default mock).

    The ``cdp`` driver is imported on demand so its optional Playwright
    dependency never blocks ``mock`` mode.
    """
    chosen = (name or os.environ.get("ARENA_DRIVER") or "mock").strip().lower()
    if chosen in ("mock", "fake", "demo"):
        return MockDriver(
            work_seconds=float(kwargs.get("work_seconds", os.environ.get("ARENA_MOCK_WORK_SECONDS", "3"))),
            delay_seconds=float(kwargs.get("delay_seconds", os.environ.get("ARENA_MOCK_DELAY_SECONDS", "1"))),
        )
    if chosen in ("cdp", "browser", "playwright"):
        from .cdp import CdpDriver

        return CdpDriver(cdp_url=kwargs.get("cdp_url"), agent_url=kwargs.get("agent_url"))
    raise ArenaError(f"unknown driver: {chosen!r} (choose 'mock' or 'cdp')")


__all__ = ["Driver", "ArenaError", "create_driver"]