"""arena.ai Agent Mode driver over Chrome DevTools Protocol (CDP).

arena.ai's Agent Mode is an authenticated web app with no public API, so this
driver drives a browser that is already logged in to arena.ai. It attaches over
CDP (Chrome started with ``--remote-debugging-port``, or Playwright's headless
chromium) and, for each spawned task, opens an Agent Mode chat, submits the
task, and polls until the agent finishes or needs input.

Each spawned session tracks its own progress in ``self._sessions``
(a dict of session_id -> live state), matching the ``spawn()/status()`` polling
model used by the MCP server. Playwright is imported lazily so the addon still
runs in ``mock`` mode without it installed.
"""

from __future__ import annotations

import os
import re
import threading
import time

from .base import Driver, ArenaError

# arena.ai is a JS SPA, so DOM selectors are heuristic and centralized here so
# they can be tuned if the UI changes.
CANDIDATE_INPUTS = [
    "textarea[placeholder]",
    "textarea",
    "[contenteditable='true']",
    "[role='textbox']",
]

CANDIDATE_SEND = [
    "button[type='submit']",
    "button[aria-label='Send']",
    "button[aria-label='Start']",
    "button[aria-label*='send' i]",
]

# The agent UI shows a terminal state we can detect by these strings.
BLOCKED_MARKERS = (
    "could you clarify",
    "does that sound",
    "may i ask",
    "could you confirm",
    "what would you like me to",
)


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class CdpDriver(Driver):
    name = "cdp"

    def __init__(
        self,
        cdp_url: str | None = None,
        agent_url: str | None = None,
    ) -> None:
        self.cdp_url = cdp_url or os.environ.get("ARENA_CDP_URL") or "http://127.0.0.1:9222"
        self.agent_url = agent_url or os.environ.get("ARENA_AGENT_URL") or "https://arena.ai/agent"
        self.poll_interval = max(0.5, float(os.environ.get("ARENA_POLL_INTERVAL", "2")))
        self.idle_confirm_loops = int(os.environ.get("ARENA_IDLE_LOOPS", "4"))
        self.hard_timeout = float(os.environ.get("ARENA_HARD_TIMEOUT", "3600"))
        self.dry_run = _env_bool("ARENA_DRY_RUN", False)
        self.headless = _env_bool("ARENA_HEADLESS", False)

        self._lock = threading.RLock()
        self._browser = None
        self._sessions: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}

    # -- connection -------------------------------------------------------
    def _ensure_browser(self):  # pragma: no cover - exercised with real browser
        if self._browser is not None:
            return self._browser
        with self._lock:
            if self._browser is not None:
                return self._browser
            try:
                import playwright.sync_api as pw
            except Exception as exc:
                raise ArenaError(
                    "playwright is not installed. Run: python -m pip install "
                    "'playwright' and `playwright install chromium`, or use "
                    "ARENA_DRIVER=mock."
                ) from exc
            self._pw = pw
            self._browser = pw.chromium.connect_over_cdp(self.cdp_url)
            if self._browser is None:
                raise ArenaError(f"could not connect to browser at {self.cdp_url}")
            return self._browser

    def _context(self):  # pragma: no cover
        browser = self._browser
        contexts = getattr(browser, "contexts", None)
        if contexts and len(contexts) > 0:
            return contexts[0]
        return browser.new_context()

    # -- DOM helpers ------------------------------------------------------
    def _find_input(self, page):  # pragma: no cover
        for sel in CANDIDATE_INPUTS:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    return el
            except Exception:
                continue
        raise ArenaError("Agent Mode input box not found; arena.ai UI may have changed.")

    def _find_send(self, page):  # pragma: no cover
        for sel in CANDIDATE_SEND:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    return el
            except Exception:
                continue
        raise ArenaError("Send button not found; arena.ai UI may have changed.")

    @staticmethod
    def _page_text(page) -> str:  # pragma: no cover
        try:
            return page.locator("body").inner_text(timeout=1500)
        except Exception:
            return ""

    # -- worker -----------------------------------------------------------
    def _mark(self, sid: str, **fields) -> None:
        with self._lock:
            rec = self._sessions.get(sid)
            if rec is None:
                return
            rec.update(fields)

    def _run_session(self, sid: str, task: str, upload_files: list) -> None:
        self._mark(sid, status="starting")
        try:
            self._worker(sid, task, upload_files)
        except Exception as exc:  # noqa: BLE001 - surface any failure
            self._mark(sid, status="failed", result=f"{type(exc).__name__}: {exc}")

    def _worker(self, sid: str, task: str, upload_files: list) -> None:
        pw = self._ensure_browser()
        page = None
        try:
            page = self._context().new_page()
            self._mark(sid, status="running", output="opening Agent Mode\n")
            page.goto(self.agent_url, wait_until="domcontentloaded", timeout=60000)

            inp = None
            for _ in range(30):
                try:
                    inp = self._find_input(page)
                    break
                except ArenaError:
                    time.sleep(1)
            if inp is None:
                raise ArenaError("Agent Mode did not become ready within 30s")

            self._try_upload(page, upload_files)
            inp.click()
            inp.fill(task)

            if self.dry_run:
                self._mark(sid, status="completed", result="ARENA_DRY_RUN: task filled, not sent")
                return

            self._find_send(page).click()
            self._mark(sid, status="running", output="submitted to Agent Mode\n")
            self._poll(page, sid, task)
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def _try_upload(self, page, upload_files) -> None:  # pragma: no cover
        files = [f for f in (upload_files or []) if os.path.isfile(f)]
        if not files:
            return
        try:
            fi = page.locator("input[type='file']")
            if fi.count():
                fi.set_input_files(files)
        except Exception:
            # No file input exposed; upload must be done via the UI.
            pass

    def _poll(self, page, sid: str, task: str) -> None:  # pragma: no cover
        last_text = ""
        stable = 0
        start = time.time()
        while True:
            text = self._page_text(page)
            lowered = text.lower()
            if text != last_text:
                if len(text) > len(last_text):
                    self._mark(sid, output=text[len(last_text):])
                last_text = text
                stable = 0
            else:
                stable += 1

            blocked = self._detect_blocked(lowered)
            if blocked:
                self._mark(sid, status="blocked_request", result=blocked)
                return
            if self._detect_done(page, lowered):
                self._mark(sid, status="completed", result=text)
                return
            if time.time() - start > self.hard_timeout:
                self._mark(sid, status="timeout", result=text)
                return
            if stable >= self.idle_confirm_loops and len(text.strip()) > 50:
                self._mark(sid, status="completed", result=text)
                return
            time.sleep(self.poll_interval)

    def _detect_done(self, page, lowered_text: str) -> bool:  # pragma: no cover
        try:
            stop = page.get_by_role("button", name=re.compile("stop", re.I))
            if stop.count() and stop.first.is_visible():
                return False
        except Exception:
            pass
        return "give feedback" in lowered_text or "keep working" in lowered_text

    @staticmethod
    def _detect_blocked(lowered_text: str) -> str | None:
        for m in BLOCKED_MARKERS:
            if m in lowered_text:
                return f"Arena agent paused to ask a clarifying question (marker: '{m}')."
        return None

    # -- Driver interface -------------------------------------------------
    def spawn(self, task: str, upload_files: list | None = None) -> dict:
        if not task or not task.strip():
            raise ArenaError("task must be a non-empty string")
        sid = f"cdp-{int(time.time() * 1000)}"
        with self._lock:
            self._sessions[sid] = {
                "status": "pending",
                "output": "",
                "result": None,
            }
            t = threading.Thread(
                target=self._run_session, args=(sid, task.strip(), upload_files or []), daemon=True
            )
            self._threads[sid] = t
        t.start()
        return {"session_id": sid}

    def status(self, session_id: str) -> dict:
        with self._lock:
            rec = self._sessions.get(session_id)
            if rec is None:
                return {"status": "unknown"}
            return dict(rec)

    def wait(self, session_id: str, timeout_secs: float) -> dict:
        deadline = time.time() + max(0.0, float(timeout_secs))
        snap = self.status(session_id)
        while time.time() < deadline and snap["status"] in ("pending", "running", "starting"):
            snap = self.status(session_id)
            time.sleep(1.0)
        return snap

    def download_workspace(self, session_id: str, dest_dir: str) -> list:
        # Workspace files live inside the arena session. Downloading them
        # requires triggering arena's /download-workspace flow in the browser,
        # which is best paired with the operator's own session. This returns an
        # empty list by default; see README for enabling a document downloader.
        return []

    def list_sessions(self) -> list:
        with self._lock:
            return [
                {"id": sid, "status": rec.get("status"), "task": rec.get("task")}
                for sid, rec in self._sessions.items()
            ]