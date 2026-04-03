"""
Parser worker — all-in-one: takes email → registers on tracker → parses → downloads.

Flow per cycle:
  1. Claim fresh email from DB (thread-safe, no two workers get the same one)
  2. Register new tracker account using that email (with CAPTCHA via Telegram)
  3. Log in to the tracker
  4. Scan pages, extract tags, download matching torrents
  5. When download limit hit → go to step 1 with new email
  6. Topic queue and page position survive account switches

Each worker runs in its own visible browser window for debugging.
"""

import logging
import random
import string
from pathlib import Path
from playwright.sync_api import BrowserContext

from .base_worker import BaseWorker
from ..db.repository import Repository
from ..browser.browser_manager import WorkerBrowser
from ..browser.tracker_actions import (
    login, register_on_tracker, get_topic_list, extract_topic_details,
    download_torrent_file, download_cover_image, human_delay,
)
from ..browser.tracker_profiles import get_profile, TrackerProfile
from ..api.telegram_captcha import TelegramCaptchaSolver
from ..config_manager import load_config, get_download_dir

log = logging.getLogger(__name__)


def _random_username(length=10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _random_password(length=12) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length)) + "!"


class ParserWorker(BaseWorker):
    def __init__(self, worker_id: str, repo: Repository, proxy: dict | None = None):
        super().__init__(worker_id, name=f"Parser-{worker_id}")
        self.repo = repo
        self.proxy = proxy
        self.browser: WorkerBrowser | None = None  # created in work() thread
        self.ctx: BrowserContext | None = None
        self.page = None
        self.downloads_remaining = 0
        self.captcha_solver: TelegramCaptchaSolver | None = None

        # Current email being used
        self._current_email_id: int | None = None

        # State that survives account switches
        self._current_page_num: int | None = None
        self._topic_queue: list[dict] = []
        self._pending_download: dict | None = None

    # ── Account lifecycle: email → register → login ─────────────

    def _new_account_cycle(self, cfg: dict, profile: TrackerProfile) -> bool:
        """
        Full cycle: take email from DB → register on tracker → log in.
        Returns True on success. Email is marked IN_USE atomically (thread-safe).
        """
        # Close old browser context
        if self.ctx:
            try:
                self.ctx.close()
            except Exception:
                pass
            self.ctx = None
            self.page = None

        # 1. Get fresh email (thread-safe — claimed atomically)
        email = self.repo.get_fresh_email()
        if not email:
            self._emit_status("No fresh emails — add more in the Emails tab!")
            return False

        self._current_email_id = email.id
        self._emit_status(f"Using email: {email.email}")

        # 2. Create browser context (visible window, unique fingerprint)
        self.ctx = self.browser.create_context(proxy=self.proxy)

        # Set window title so user can identify which worker is which
        first_page = self.ctx.new_page()
        first_page.evaluate(f"document.title = 'Worker: {self.worker_id}'")

        # 3. Register on tracker
        username = _random_username()
        password = _random_password()

        self._emit_status(f"[{profile.name}] Registering {username} with {email.email}...")
        success = register_on_tracker(
            self.ctx, profile, username, password, email.email,
            captcha_solver=self.captcha_solver,
            worker_id=self.worker_id,
            status_callback=self._emit_status,
        )

        first_page.close()

        if not success:
            self._emit_status(f"Registration failed for {email.email}")
            self.repo.mark_email_used(email.id)
            try:
                self.ctx.close()
            except Exception:
                pass
            self.ctx = None
            return False

        # 4. Log in with new account
        self._emit_status(f"[{profile.name}] Logging in as {username}...")
        try:
            self.page = login(self.ctx, profile, username, password)
        except Exception as e:
            self.log.error(f"Login failed after registration: {e}")
            self._emit_status(f"Login failed: {e}")
            self.repo.mark_email_used(email.id)
            try:
                self.ctx.close()
            except Exception:
                pass
            self.ctx = None
            return False

        # Set page title for identification
        try:
            self.page.evaluate(f"document.title = '{self.worker_id} | {username} | ' + document.title")
        except Exception:
            pass

        self.downloads_remaining = cfg["tracker"]["max_downloads_per_account"]
        self.repo.mark_email_used(email.id)
        self._emit_status(f"Ready: {username} ({self.downloads_remaining} downloads)")
        return True

    def _ensure_account(self, cfg: dict, profile: TrackerProfile) -> bool:
        """Ensure we have a working logged-in session. Retry loop."""
        if self.page is not None and self.downloads_remaining > 0:
            return True

        while not self.should_stop:
            if self._new_account_cycle(cfg, profile):
                return True
            self._emit_status("Retrying in 15s...")
            self._stop_event.wait(timeout=15)
        return False

    def _switch_account(self, cfg: dict, profile: TrackerProfile) -> bool:
        """Switch to new email+account when download limit hit. Preserves topic queue."""
        self._emit_status("Download limit reached — getting new email...")
        self.page = None
        self.downloads_remaining = 0
        return self._ensure_account(cfg, profile)

    # ── Main work loop ──────────────────────────────────────────

    def work(self):
        cfg = load_config()
        tracker_name = cfg["tracker"].get("profile", "rutracker")
        profile = get_profile(tracker_name)
        category_id = cfg["tracker"]["category_id"]
        download_tags = cfg["tags"]["download_tags"]
        record_tags = cfg["tags"]["record_tags"]
        download_dir = get_download_dir(cfg)

        # Init Telegram captcha solver
        tg = cfg.get("telegram", {})
        if tg.get("bot_token") and tg.get("chat_id"):
            self.captcha_solver = TelegramCaptchaSolver(tg["bot_token"], tg["chat_id"])

        if not category_id:
            self._emit_status("No category ID configured")
            return

        # Launch browser in THIS thread (Playwright requirement)
        self.browser = WorkerBrowser(self.worker_id)
        self.browser.start()

        try:
            self._run_parse_loop(cfg, profile, category_id, download_tags, record_tags, download_dir)
        finally:
            # Always clean up browser
            if self.ctx:
                try:
                    self.ctx.close()
                except Exception:
                    pass
            self.browser.stop()

    def _run_parse_loop(self, cfg, profile, category_id, download_tags, record_tags, download_dir):
        # Get first account
        if not self._ensure_account(cfg, profile):
            return

        while not self.should_stop:
            self.wait_if_paused()
            if self.should_stop:
                break

            # Handle pending download after account switch
            if self._pending_download:
                topic = self._pending_download
                self._pending_download = None
                dl_ok = self._do_download(cfg, profile, topic["topic_id"], topic["cover_url"], download_dir)
                if not dl_ok:
                    if not self._switch_account(cfg, profile):
                        break
                    self._pending_download = topic
                    continue
                continue

            # Get next page if queue is empty
            if not self._topic_queue:
                if self._current_page_num is not None:
                    self.repo.complete_page(category_id, self._current_page_num, self.worker_id)
                    self._emit_status(f"Page {self._current_page_num} completed")

                self._current_page_num = self.repo.claim_next_page(category_id, self.worker_id)
                if self._current_page_num is None:
                    self._emit_status("All pages processed!")
                    break

                self._emit_status(f"Scanning page {self._current_page_num}...")
                try:
                    topics = get_topic_list(self.page, profile, category_id, self._current_page_num)
                    self._topic_queue = list(topics)
                    self._emit_status(f"Page {self._current_page_num}: {len(topics)} topics")
                except Exception as e:
                    self.log.error(f"Failed to scan page {self._current_page_num}: {e}")
                    self._emit_status(f"Scan error: {e}")
                    self.repo.release_page(category_id, self.worker_id)
                    self._current_page_num = None
                    continue

            # Process next topic
            if self._topic_queue:
                topic = self._topic_queue.pop(0)
                self._process_topic(cfg, profile, topic, category_id, download_tags, record_tags, download_dir)

        # Release unfinished page
        if self._current_page_num is not None and self._topic_queue:
            self.repo.release_page(category_id, self.worker_id)

    # ── Topic processing ────────────────────────────────────────

    def _process_topic(self, cfg, profile, topic, category_id, download_tags, record_tags, download_dir):
        topic_id = topic["topic_id"]
        title = topic["title"]

        t = self.repo.add_torrent(topic_id, title, category_id, self._current_page_num)
        if t is None:
            return

        self._emit_status(f"Topic {topic_id}: {title[:50]}...")

        try:
            details = extract_topic_details(self.page, profile, topic_id, download_tags, record_tags)
        except Exception as e:
            self.log.error(f"Failed to extract topic {topic_id}: {e}")
            self.repo.mark_torrent_error(topic_id)
            return

        self.repo.update_torrent_tags(
            topic_id,
            details["matched_download_tags"],
            details["matched_record_tags"],
            details["description"],
            details["cover_url"],
        )

        if details["matched_download_tags"]:
            if self.downloads_remaining <= 0:
                self._pending_download = {"topic_id": topic_id, "cover_url": details["cover_url"]}
                if not self._switch_account(cfg, profile):
                    return
                return

            self._do_download(cfg, profile, topic_id, details["cover_url"], download_dir)

        human_delay(0.5, 1.5)

    def _do_download(self, cfg, profile, topic_id, cover_url, download_dir) -> bool:
        if self.downloads_remaining <= 0:
            return False

        self.repo.mark_torrent_downloading(topic_id)
        self._emit_status(f"Downloading {topic_id}...")

        file_path = download_torrent_file(self.page, profile, topic_id, download_dir)
        if file_path:
            cover_path = download_cover_image(self.page, cover_url, download_dir, topic_id)
            self.repo.mark_torrent_downloaded(topic_id, file_path, cover_path)
            self.downloads_remaining -= 1
            self._emit_status(f"Downloaded {topic_id} ({self.downloads_remaining} left)")
            return True
        else:
            self.repo.mark_torrent_error(topic_id)
            self._emit_status(f"Download failed: {topic_id}")
            return True
