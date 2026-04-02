"""
Account registration worker — registers new accounts on the tracker using emails from NotLetters.

Flow:
1. Get fresh email from DB (purchased by EmailWorker via NotLetters API)
2. Open tracker registration page in browser
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
from ..browser.tracker_actions import human_delay
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
        base_url = cfg["tracker"]["base_url"]

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

            self._emit_status(f"Registering tracker account with {email.email}...")

            ctx = self.browser.create_context(proxy=self.proxy)
            try:
                result = self._register_on_tracker(ctx, base_url, email.email, email.password)
                if result:
                    self.repo.add_tracker_account(
                        result["username"], result["password"], email.id
                    )
                    self.repo.mark_email_used(email.id)
                    self._emit_status(f"Registered: {result['username']}")
                else:
                    self._emit_status("Tracker registration failed — retrying...")
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

    def _register_on_tracker(self, ctx: BrowserContext, base_url: str,
                              email: str, email_password: str) -> dict | None:
        """
        Register on the tracker site.
        Returns {"username": str, "password": str} or None.
        """
        page = ctx.new_page()
        try:
            username = _random_username()
            password = _random_password()

            page.goto(f"{base_url}/forum/profile.php?mode=register", wait_until="domcontentloaded")
            human_delay(2, 4)

            # Accept rules if there's an agreement page
            try:
                agree_btn = page.query_selector("input[name='agree'], #rules-btn, a.agree")
                if agree_btn:
                    agree_btn.click()
                    page.wait_for_load_state("domcontentloaded")
                    human_delay(1, 2)
            except Exception:
                pass

            # Fill registration form
            page.fill("input[name='username'], #username", username)
            human_delay(0.3, 0.8)

            page.fill("input[name='new_password'], #new_password", password)
            human_delay(0.3, 0.8)

            page.fill("input[name='password_confirm'], #password_confirm", password)
            human_delay(0.3, 0.8)

            page.fill("input[name='email'], #email", email)
            human_delay(0.3, 0.8)

            # Handle CAPTCHA if present — wait for manual solve in visible window
            captcha = page.query_selector("img[src*='captcha'], .captcha-img, #cap_img")
            if captcha:
                self._emit_status("CAPTCHA detected — solve it in the browser window (90s)...")
                try:
                    page.wait_for_function(
                        "() => document.querySelector('input[name=\"cap_code\"]')?.value.length > 3",
                        timeout=90000,
                    )
                except Exception:
                    human_delay(5, 10)

            # Submit
            page.click("input[type='submit'][name='submit'], button[type='submit']")
            page.wait_for_load_state("domcontentloaded")
            human_delay(2, 4)

            # Check if email confirmation is needed
            page_text = page.inner_text("body")[:500].lower()
            needs_confirmation = (
                "подтвер" in page_text or "confirm" in page_text or
                "актив" in page_text or "email" in page_text
            )

            if needs_confirmation and self.nl_client:
                self._emit_status(f"Waiting for confirmation email on {email}...")
                confirm_url = self._wait_for_confirmation_link(email, email_password, base_url)
                if confirm_url:
                    self._emit_status("Confirming email...")
                    page.goto(confirm_url, wait_until="domcontentloaded")
                    human_delay(2, 4)
                    log.info(f"Email confirmed for {username}")
                else:
                    log.warning(f"No confirmation email received for {username}")

            # Verify registration succeeded
            if "profile.php" in page.url or "login" in page.url.lower() or needs_confirmation:
                log.info(f"Registered tracker account: {username}")
                page.close()
                return {"username": username, "password": password}
            else:
                error_text = page.inner_text("body")[:200]
                log.warning(f"Registration may have failed: {error_text}")
                page.close()
                return None

        except Exception as e:
            log.error(f"Tracker registration error: {e}")
            try:
                page.close()
            except Exception:
                pass
            return None

    def _wait_for_confirmation_link(self, email: str, password: str, base_url: str) -> str | None:
        """
        Poll NotLetters mailbox for a confirmation email from the tracker.
        Extract and return the confirmation URL.
        """
        if not self.nl_client:
            return None

        # Search for emails from the tracker domain
        domain = base_url.replace("https://", "").replace("http://", "").split("/")[0]
        letter = self.nl_client.wait_for_letter(
            email, password,
            search=domain,
            timeout=120,
            interval=5,
        )

        if not letter:
            return None

        # Extract confirmation link from email HTML/text
        # Look for URLs containing "confirm", "activate", "verify"
        content = letter.html or letter.text
        urls = re.findall(r'https?://[^\s<>"\']+', content)
        for url in urls:
            if any(kw in url.lower() for kw in ["confirm", "activ", "verify", "profile.php"]):
                log.info(f"Found confirmation link: {url}")
                return url

        # Fallback: return first URL that matches tracker domain
        for url in urls:
            if domain in url:
                return url

        log.warning(f"No confirmation link found in email for {email}")
        return None
