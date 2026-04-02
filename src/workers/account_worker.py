"""
Account registration worker — registers new accounts on the tracker using emails from NotLetters.

Flow:
1. Get fresh email from DB (purchased by EmailWorker via NotLetters API)
2. Open tracker registration page in browser (uses tracker profile for selectors)
3. Fill form, handle CAPTCHA (manual), submit
4. Wait for confirmation email via NotLetters API
5. Click confirmation link in browser
6. Store account in DB
"""

import logging
import re
import random
import string
from playwright.sync_api import BrowserContext

from .base_worker import BaseWorker
from ..browser.browser_manager import BrowserManager
from ..browser.tracker_actions import register_on_tracker, human_delay
from ..browser.tracker_profiles import get_profile, TrackerProfile
from ..api.notletters import NotLettersClient
from ..db.repository import Repository
from ..config_manager import load_config

log = logging.getLogger(__name__)


def _random_username(length=10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _random_password(length=12) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length)) + "!"


class AccountWorker(BaseWorker):
    def __init__(self, worker_id: str, browser_manager: BrowserManager, repo: Repository,
                 proxy: dict | None = None, min_pool_size: int = 3):
        super().__init__(worker_id, name=f"AcctReg-{worker_id}")
        self.browser = browser_manager
        self.repo = repo
        self.proxy = proxy
        self.min_pool_size = min_pool_size
        self.nl_client: NotLettersClient | None = None

    def work(self):
        cfg = load_config()
        tracker_name = cfg["tracker"].get("profile", "rutracker")
        profile = get_profile(tracker_name)

        # Init NotLetters client for reading confirmation emails
        token = cfg.get("notletters", {}).get("api_token", "")
        if token:
            self.nl_client = NotLettersClient(token)

        while not self.should_stop:
            self.wait_if_paused()
            if self.should_stop:
                break

            stats = self.repo.get_stats()
            fresh_accounts = stats["fresh_accounts"]

            if fresh_accounts >= self.min_pool_size:
                self._emit_status(f"Account pool OK ({fresh_accounts} fresh) — waiting...")
                self._stop_event.wait(timeout=30)
                continue

            # Need a fresh email first
            email = self.repo.get_fresh_email()
            if not email:
                self._emit_status("No fresh emails — waiting for email worker...")
                self._stop_event.wait(timeout=15)
                continue

            self._emit_status(f"[{profile.name}] Registering with {email.email}...")

            ctx = self.browser.create_context(proxy=self.proxy)
            try:
                username = _random_username()
                password = _random_password()

                success = register_on_tracker(
                    ctx, profile, username, password, email.email,
                    status_callback=self._emit_status,
                )

                if success:
                    # Wait for confirmation email if needed
                    confirm_url = self._wait_for_confirmation(
                        email.email, email.password, profile
                    )
                    if confirm_url:
                        self._emit_status("Confirming email...")
                        page = ctx.new_page()
                        page.goto(confirm_url, wait_until="domcontentloaded")
                        human_delay(2, 4)
                        page.close()

                    self.repo.add_tracker_account(username, password, email.id)
                    self.repo.mark_email_used(email.id)
                    self._emit_status(f"[{profile.name}] Registered: {username}")
                else:
                    self._emit_status("Registration failed — retrying...")
                    self._stop_event.wait(timeout=15)
            except Exception as e:
                self.log.error(f"Account registration error: {e}")
                self._emit_status(f"Error: {e}")
                self._stop_event.wait(timeout=15)
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass

    def _wait_for_confirmation(self, email: str, password: str,
                                profile: TrackerProfile) -> str | None:
        """Poll NotLetters mailbox for a confirmation email. Returns link or None."""
        if not self.nl_client:
            return None

        domain = profile.base_url.replace("https://", "").replace("http://", "").split("/")[0]
        self._emit_status(f"Waiting for confirmation email from {domain}...")

        letter = self.nl_client.wait_for_letter(
            email, password,
            search=domain,
            timeout=120,
            interval=5,
        )

        if not letter:
            log.warning(f"No confirmation email received for {email}")
            return None

        # Extract confirmation link
        content = letter.html or letter.text
        urls = re.findall(r'https?://[^\s<>"\']+', content)
        for url in urls:
            if any(kw in url.lower() for kw in ["confirm", "activ", "verify", "profile.php"]):
                log.info(f"Found confirmation link: {url}")
                return url

        for url in urls:
            if domain in url:
                return url

        log.warning(f"No confirmation link found in email for {email}")
        return None
