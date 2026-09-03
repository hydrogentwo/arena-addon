"""Tests for the cdp driver's pure/heuristic logic.

These exercise the parts of ``CdpDriver`` that decide when an Arena agent has
finished or paused, using a fake page object so no Playwright or live arena.ai
connection is required.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from arena_addon.drivers.cdp import CdpDriver  # noqa: E402


class _FakeFirst:
    def __init__(self, visible):
        self._visible = visible

    def is_visible(self):
        return self._visible


class _FakeLocator:
    def __init__(self, visible):
        self.first = _FakeFirst(visible)

    def count(self):
        return 1


class _FakePage:
    def __init__(self, stop_visible):
        self._stop_visible = stop_visible

    def get_by_role(self, role, **kwargs):
        return _FakeLocator(self._stop_visible)


class DetectDoneTest(unittest.TestCase):
    def make(self, stop_visible=False):
        return CdpDriver(cdp_url="http://127.0.0.1:1", agent_url="https://arena.ai/agent")
        # unreachable URL is fine because these helper tests never connect.

    def test_terminal_marker_without_stop(self):
        d = self.make()
        page = _FakePage(stop_visible=False)
        self.assertTrue(d._detect_done(page, "wrap up and give feedback below"))

    def test_keep_working_marker(self):
        d = self.make()
        self.assertTrue(d._detect_done(_FakePage(False), "choose keep working to continue"))

    def test_stop_button_means_still_running(self):
        d = self.make()
        # Even with terminal text present, a visible Stop button means running.
        self.assertFalse(d._detect_done(_FakePage(stop_visible=True), "give feedback"))

    def test_no_marker_not_done(self):
        d = self.make()
        self.assertFalse(d._detect_done(_FakePage(False), "working on it now"))


class DetectBlockedTest(unittest.TestCase):
    def test_returns_marker_on_clarify(self):
        d = CdpDriver()
        self.assertIsNotNone(d._detect_blocked("could you clarify what you meant by x"))

    def test_none_when_plain_progress(self):
        d = CdpDriver()
        self.assertIsNone(d._detect_blocked("searching the web and writing files"))

    def test_empty_text(self):
        d = CdpDriver()
        self.assertIsNone(d._detect_blocked(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)