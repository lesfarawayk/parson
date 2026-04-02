"""Browser manager — creates Playwright browser contexts with visible windows, proxy, and fingerprint spoofing."""

import logging
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from .fingerprint import generate_fingerprint, build_stealth_script

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

    def create_context(self, proxy: dict | None = None) -> BrowserContext:
        """
        Create a new isolated browser context with a unique fingerprint.
        proxy format: {"server": "http://host:port", "username": "u", "password": "p"}
        """
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

        # Inject stealth + fingerprint overrides before any page loads
        ctx.add_init_script(build_stealth_script(fp))

        log.info(
            f"Context created: {fp['user_agent'][:60]}... | "
            f"{fp['screen']['width']}x{fp['screen']['height']} | "
            f"{fp['timezone_id']} | {fp['webgl_renderer'][:40]}..."
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
        log.info("Browser stopped")
