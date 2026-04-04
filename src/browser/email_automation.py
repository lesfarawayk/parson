"""Browser automation for email registration on Rambler."""

import re
import time
import random
import string
import logging
from typing import Optional, Tuple
from playwright.sync_api import sync_playwright, Browser, Page

from src.config.settings import AppConfig

logger = logging.getLogger(__name__)


def _random_string(length: int = 10) -> str:
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _random_password(length: int = 14) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    pwd = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice("!@#$%"),
    ]
    pwd += random.choices(chars, k=length - 4)
    random.shuffle(pwd)
    return ''.join(pwd)


class RamblerRegistration:
    """Automates Rambler email registration."""

    def __init__(self, worker_id: str, config: AppConfig, proxy: Optional[str] = None):
        self.worker_id = worker_id
        self.config = config
        self.proxy = proxy
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    def start(self):
        self._playwright = sync_playwright().start()
        launch_args = {
            'headless': self.config.browser.headless,
            'slow_mo': self.config.browser.slow_mo,
        }
        if self.proxy:
            match = re.match(
                r'(?P<proto>https?|socks[45])://(?:(?P<user>[^:]+):(?P<pass>[^@]+)@)?(?P<host>[^:]+):(?P<port>\d+)',
                self.proxy
            )
            if match:
                launch_args['proxy'] = {
                    'server': f"{match.group('proto')}://{match.group('host')}:{match.group('port')}",
                }
                if match.group('user'):
                    launch_args['proxy']['username'] = match.group('user')
                    launch_args['proxy']['password'] = match.group('pass')

        self._browser = self._playwright.chromium.launch(**launch_args)
        context = self._browser.new_context(
            viewport={'width': 1280, 'height': 900},
            locale='ru-RU',
        )
        self._page = context.new_page()

    def stop(self):
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    def _human_delay(self, min_s: float = 0.5, max_s: float = 2.0):
        time.sleep(random.uniform(min_s, max_s))

    def register_email(self) -> Optional[Tuple[str, str]]:
        """Register a new Rambler email. Returns (email, password) or None."""
        try:
            username = f"parson_{_random_string(8)}"
            password = _random_password()
            email = f"{username}@{self.config.email.domain}"

            self._page.goto("https://id.rambler.ru/account/registration", wait_until='domcontentloaded')
            self._human_delay(1, 3)

            # Fill registration form
            # Username/login field
            login_input = self._page.query_selector(
                'input[name="login"], input[data-testid="login"], #login'
            )
            if login_input:
                login_input.fill(username)
                self._human_delay()

            # Password fields
            pwd_input = self._page.query_selector(
                'input[name="password"], input[data-testid="password"], input[type="password"]'
            )
            if pwd_input:
                pwd_input.fill(password)
                self._human_delay()

            pwd_confirm = self._page.query_selector(
                'input[name="confirmPassword"], input[data-testid="confirm-password"]'
            )
            if pwd_confirm:
                pwd_confirm.fill(password)
                self._human_delay()

            # Security question (if present)
            question_select = self._page.query_selector(
                'select[name="question"], div[data-testid="question"]'
            )
            if question_select:
                question_select.click()
                self._human_delay(0.3, 0.8)
                # Select first option
                option = self._page.query_selector('option:nth-child(2), li:first-child')
                if option:
                    option.click()

            answer_input = self._page.query_selector(
                'input[name="answer"], input[data-testid="answer"]'
            )
            if answer_input:
                answer_input.fill(_random_string(8))
                self._human_delay()

            # Submit
            submit_btn = self._page.query_selector(
                'button[type="submit"], button[data-testid="submit"]'
            )
            if submit_btn:
                submit_btn.click()
                self._page.wait_for_load_state('domcontentloaded')
                self._human_delay(2, 4)

            # Check for CAPTCHA — requires manual solving or external service
            # For now, log and wait
            captcha = self._page.query_selector(
                'iframe[src*="captcha"], div[class*="captcha"], img[class*="captcha"]'
            )
            if captcha:
                logger.warning(f"[{self.worker_id}] CAPTCHA detected during email registration! "
                               f"Manual intervention or anti-captcha service needed.")
                # Wait up to 60 seconds for manual CAPTCHA solving
                time.sleep(60)

            # Verify success — check if we're redirected to mailbox
            if 'mail' in self._page.url or 'inbox' in self._page.url:
                logger.info(f"[{self.worker_id}] Email registered: {email}")
                return email, password

            # Try checking for success message
            success = self._page.query_selector('.success, .registration-success')
            if success:
                logger.info(f"[{self.worker_id}] Email registered: {email}")
                return email, password

            logger.warning(f"[{self.worker_id}] Email registration may have failed for {email}")
            return email, password  # Return anyway, tracker reg will validate

        except Exception as e:
            logger.error(f"[{self.worker_id}] Email registration error: {e}")
            return None


class TrackerRegistration:
    """Automates tracker account registration."""

    def __init__(self, worker_id: str, config: AppConfig, proxy: Optional[str] = None):
        self.worker_id = worker_id
        self.config = config
        self.proxy = proxy
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    def start(self):
        self._playwright = sync_playwright().start()
        launch_args = {
            'headless': self.config.browser.headless,
            'slow_mo': self.config.browser.slow_mo,
        }
        self._browser = self._playwright.chromium.launch(**launch_args)
        context = self._browser.new_context(
            viewport={'width': 1280, 'height': 900},
            locale='ru-RU',
        )
        self._page = context.new_page()

    def stop(self):
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    def _human_delay(self, min_s: float = 0.5, max_s: float = 2.0):
        time.sleep(random.uniform(min_s, max_s))

    def register_account(self, email: str, email_password: str) -> Optional[Tuple[str, str]]:
        """Register a new tracker account using the given email.
        Returns (username, password) or None."""
        try:
            username = f"user_{_random_string(8)}"
            password = _random_password()

            reg_url = f"{self.config.tracker.base_url}/forum/profile.php?mode=register"
            self._page.goto(reg_url, wait_until='domcontentloaded')
            self._human_delay(1, 3)

            # Accept rules if present
            agree_btn = self._page.query_selector(
                'input[name="agree"], button:has-text("Согласен"), '
                'a:has-text("Согласен"), input[value="Согласен"]'
            )
            if agree_btn:
                agree_btn.click()
                self._page.wait_for_load_state('domcontentloaded')
                self._human_delay()

            # Fill registration form
            self._page.fill(
                'input[name="username"], input[name="reg_username"]', username
            )
            self._human_delay()

            self._page.fill(
                'input[name="new_password"], input[name="reg_password"]', password
            )
            self._human_delay()

            self._page.fill(
                'input[name="password_confirm"], input[name="reg_password_confirm"]', password
            )
            self._human_delay()

            self._page.fill(
                'input[name="email"], input[name="reg_email"]', email
            )
            self._human_delay()

            # CAPTCHA handling
            captcha = self._page.query_selector(
                'img[src*="captcha"], iframe[src*="captcha"], div[class*="captcha"]'
            )
            if captcha:
                logger.warning(f"[{self.worker_id}] CAPTCHA on tracker registration! "
                               f"Manual intervention needed.")
                time.sleep(60)

            # Submit
            submit = self._page.query_selector(
                'input[type="submit"][name="submit"], '
                'button[type="submit"], input[value="Отправить"]'
            )
            if submit:
                submit.click()
                self._page.wait_for_load_state('domcontentloaded')
                self._human_delay(2, 4)

            logger.info(f"[{self.worker_id}] Tracker account registered: {username}")
            return username, password

        except Exception as e:
            logger.error(f"[{self.worker_id}] Tracker registration error: {e}")
            return None
