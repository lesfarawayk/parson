"""Playwright-based browser automation for the tracker."""

import os
import re
import time
import random
import logging
from typing import Optional, List, Dict
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from src.config.settings import AppConfig

logger = logging.getLogger(__name__)


class TrackerBrowser:
    """Manages a browser instance that emulates human interaction with the tracker."""

    def __init__(self, worker_id: str, config: AppConfig, proxy: Optional[str] = None):
        self.worker_id = worker_id
        self.config = config
        self.proxy = proxy
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def page(self) -> Optional[Page]:
        return self._page

    def start(self):
        """Launch browser with visible window."""
        self._playwright = sync_playwright().start()

        launch_args = {
            'headless': self.config.browser.headless,
            'slow_mo': self.config.browser.slow_mo,
        }

        # Proxy setup
        if self.proxy:
            proxy_parts = self._parse_proxy(self.proxy)
            if proxy_parts:
                launch_args['proxy'] = proxy_parts

        self._browser = self._playwright.chromium.launch(**launch_args)
        self._context = self._browser.new_context(
            viewport={'width': 1280, 'height': 900},
            user_agent=self._get_user_agent(),
            locale='ru-RU',
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(30000)
        logger.info(f"[{self.worker_id}] Browser started")

    def stop(self):
        """Close browser."""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as e:
            logger.error(f"[{self.worker_id}] Error closing browser: {e}")

    def _parse_proxy(self, proxy_url: str) -> Optional[dict]:
        """Parse proxy URL into Playwright proxy config."""
        match = re.match(
            r'(?P<protocol>https?|socks[45])://(?:(?P<user>[^:]+):(?P<pass>[^@]+)@)?(?P<host>[^:]+):(?P<port>\d+)',
            proxy_url
        )
        if not match:
            return None
        result = {
            'server': f"{match.group('protocol')}://{match.group('host')}:{match.group('port')}"
        }
        if match.group('user'):
            result['username'] = match.group('user')
            result['password'] = match.group('pass')
        return result

    def _get_user_agent(self) -> str:
        agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]
        return random.choice(agents)

    def _human_delay(self, min_s: Optional[float] = None, max_s: Optional[float] = None):
        """Random delay to emulate human behavior."""
        min_s = min_s or self.config.workers.action_delay_min
        max_s = max_s or self.config.workers.action_delay_max
        time.sleep(random.uniform(min_s, max_s))

    # ── Tracker interactions ──

    def login(self, username: str, password: str) -> bool:
        """Log into the tracker. Returns True on success."""
        try:
            login_url = f"{self.config.tracker.base_url}/forum/login.php"
            self._page.goto(login_url, wait_until='domcontentloaded')
            self._human_delay()

            # Fill login form
            self._page.fill('#login-form-login-user-name, input[name="login_username"]', username)
            self._human_delay(0.5, 1.5)
            self._page.fill('#login-form-login-password, input[name="login_password"]', password)
            self._human_delay(0.5, 1.0)

            # Submit
            self._page.click('#login-form-submit, input[name="login"]')
            self._page.wait_for_load_state('domcontentloaded')
            self._human_delay()

            # Check if logged in by looking for logged-in indicators
            logged_in = self._page.query_selector('.logged-in-username, #logged-in-username, .topmenu a[href*="profile"]')
            if logged_in:
                logger.info(f"[{self.worker_id}] Logged in as {username}")
                return True

            # Fallback: check we're not still on login page
            if 'login.php' not in self._page.url:
                logger.info(f"[{self.worker_id}] Likely logged in as {username} (redirected)")
                return True

            logger.warning(f"[{self.worker_id}] Login failed for {username}")
            return False

        except Exception as e:
            logger.error(f"[{self.worker_id}] Login error: {e}")
            return False

    def logout(self):
        """Log out from the tracker."""
        try:
            self._page.goto(
                f"{self.config.tracker.base_url}/forum/login.php?logout=1",
                wait_until='domcontentloaded'
            )
            self._human_delay()
            logger.info(f"[{self.worker_id}] Logged out")
        except Exception as e:
            logger.warning(f"[{self.worker_id}] Logout error: {e}")

    def get_category_page(self, page_number: int) -> List[Dict]:
        """Navigate to a category page and extract torrent listings.
        Returns list of {tracker_id, title, page_url}."""
        try:
            start = (page_number - 1) * 50  # rutracker uses &start= offset
            url = (
                f"{self.config.tracker.forum_url}"
                f"?f={self.config.tracker.category_id}&start={start}"
            )
            self._page.goto(url, wait_until='domcontentloaded')
            self._human_delay(
                self.config.workers.page_delay_min,
                self.config.workers.page_delay_max
            )

            # Parse torrent topic links
            topics = self._page.query_selector_all(
                'tr.hl-tr .torTopic a.torTopic, '
                'tr.hl-tr td.t-title-col a, '
                'table.forumline tr td.row1 a[href*="viewtopic"]'
            )

            results = []
            for topic in topics:
                href = topic.get_attribute('href') or ''
                title = topic.inner_text().strip()
                # Extract topic ID from URL
                tid_match = re.search(r't=(\d+)', href)
                if tid_match and title:
                    tracker_id = tid_match.group(1)
                    page_url = f"{self.config.tracker.base_url}/forum/viewtopic.php?t={tracker_id}"
                    results.append({
                        'tracker_id': tracker_id,
                        'title': title,
                        'page_url': page_url,
                    })

            logger.info(f"[{self.worker_id}] Page {page_number}: found {len(results)} topics")
            return results

        except Exception as e:
            logger.error(f"[{self.worker_id}] Error getting page {page_number}: {e}")
            return []

    def get_torrent_details(self, page_url: str) -> Optional[Dict]:
        """Open a torrent page and extract details: description, tags, cover image."""
        try:
            self._page.goto(page_url, wait_until='domcontentloaded')
            self._human_delay()

            # Extract post body (description)
            post_body = self._page.query_selector('.post_body, .post-body, td.message')
            description = post_body.inner_text().strip() if post_body else ""

            # Extract cover image
            cover_url = None
            cover_img = self._page.query_selector(
                '.post_body img.postImg, .post_body var.postImg, '
                '.post-body img[src*="pic"], .postbody img'
            )
            if cover_img:
                cover_url = (
                    cover_img.get_attribute('src')
                    or cover_img.get_attribute('title')
                    or cover_img.get_attribute('data-src')
                )

            # Also try var.postImg (rutracker wraps images in <var>)
            if not cover_url:
                var_img = self._page.query_selector('.post_body var.postImg')
                if var_img:
                    cover_url = var_img.get_attribute('title')

            return {
                'description': description,
                'cover_url': cover_url,
            }

        except Exception as e:
            logger.error(f"[{self.worker_id}] Error getting details from {page_url}: {e}")
            return None

    def download_torrent(self, page_url: str, save_dir: str) -> Optional[str]:
        """Download the .torrent file from the topic page.
        Returns the saved file path or None."""
        try:
            # Make sure we're on the topic page
            if self._page.url != page_url:
                self._page.goto(page_url, wait_until='domcontentloaded')
                self._human_delay()

            # Find download link
            dl_link = self._page.query_selector(
                'a.dl-stub, a.dl-link, a[href*="dl.php"]'
            )
            if not dl_link:
                logger.warning(f"[{self.worker_id}] No download link found on {page_url}")
                return None

            os.makedirs(save_dir, exist_ok=True)

            # Start download
            with self._page.expect_download(timeout=60000) as download_info:
                dl_link.click()
            download = download_info.value

            # Save file
            filename = download.suggested_filename or "torrent.torrent"
            filepath = os.path.join(save_dir, filename)
            download.save_as(filepath)

            logger.info(f"[{self.worker_id}] Downloaded: {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"[{self.worker_id}] Download error: {e}")
            return None

    def save_cover_image(self, cover_url: str, save_dir: str, tracker_id: str) -> Optional[str]:
        """Download and save cover image."""
        if not cover_url:
            return None
        try:
            os.makedirs(save_dir, exist_ok=True)
            ext = os.path.splitext(cover_url.split('?')[0])[-1] or '.jpg'
            filepath = os.path.join(save_dir, f"cover_{tracker_id}{ext}")

            response = self._page.request.get(cover_url)
            if response.ok:
                with open(filepath, 'wb') as f:
                    f.write(response.body())
                return filepath
        except Exception as e:
            logger.warning(f"[{self.worker_id}] Cover download error: {e}")
        return None
