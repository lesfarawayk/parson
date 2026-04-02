"""Browser manager — creates Playwright browser contexts with visible windows and proxy support."""

import logging
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

log = logging.getLogger(__name__)


class BrowserManager:
    """Manages a Playwright Chromium instance with multiple contexts (one per worker)."""

    def __init__(self):
        self._pw = None
        self._browser: Browser | None = None

    def start(self):
        """Launch the browser (headed mode so the user can see the windows)."""
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        log.info("Browser launched (headed mode)")

    def create_context(self, proxy: dict | None = None, user_agent: str | None = None) -> BrowserContext:
        """
        Create a new isolated browser context.
        proxy format: {"server": "http://host:port", "username": "u", "password": "p"}
        """
        opts = {}
        if proxy:
            opts["proxy"] = proxy
        if user_agent:
            opts["user_agent"] = user_agent
        else:
            opts["user_agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        opts["viewport"] = {"width": 1280, "height": 800}
        opts["locale"] = "ru-RU"

        ctx = self._browser.new_context(**opts)
        # Stealth tweaks
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
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
        log.info("Browser stopped")
