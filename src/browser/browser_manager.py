"""
Browser manager — each worker gets its own Playwright + Browser instance.

Playwright's sync API is bound to the thread that created it (greenlet).
Since workers run in separate threads, each must own its own Playwright instance.
"""

import logging
from playwright.sync_api import sync_playwright, Browser, BrowserContext
from .fingerprint import generate_fingerprint, build_stealth_script

log = logging.getLogger(__name__)


class WorkerBrowser:
    """
    Per-worker browser instance. Must be created AND used in the same thread.
    Call start() at the beginning of work(), stop() at the end.
    """

    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self._pw = None
        self._browser: Browser | None = None

    def start(self):
        """Launch Playwright + Chromium in this thread."""
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        log.info(f"[{self.worker_id}] Browser launched")

    def create_context(self, proxy: dict | None = None) -> BrowserContext:
        """Create a new isolated browser context with a unique fingerprint."""
        fp = generate_fingerprint()

        opts = {
            "user_agent": fp["user_agent"],
            "viewport": fp["viewport"],
            "screen": fp["screen"],
            "locale": fp["locale"],
            "timezone_id": fp["timezone_id"],
            "color_scheme": "light",
        }
        if proxy:
            opts["proxy"] = proxy

        ctx = self._browser.new_context(**opts)
        ctx.add_init_script(build_stealth_script(fp))

        log.info(
            f"[{self.worker_id}] Context: {fp['user_agent'][:50]}... | "
            f"{fp['screen']['width']}x{fp['screen']['height']} | {fp['timezone_id']}"
        )
        return ctx

    def stop(self):
        """Shut down browser and Playwright."""
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        log.info(f"[{self.worker_id}] Browser stopped")
