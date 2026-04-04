"""
Parser worker — takes email/account → logs in → scans pages → saves structured data to DB.

Flow:
  1. Claim fresh email from DB (or use as tracker login if skip_registration)
  2. Optionally register new tracker account
  3. Log in to the tracker
  4. Scan category pages, parse titles, filter by format
  5. For matching topics: open page, extract all data, download cover, save to DB
  6. No torrent downloading — that's for separate download workers later

Each worker runs in its own visible browser window for debugging.
"""

import json
import logging
import random
import string
from pathlib import Path
from playwright.sync_api import BrowserContext

from .base_worker import BaseWorker
from ..db.repository import Repository
from ..browser.browser_manager import WorkerBrowser
from ..browser.tracker_actions import (
    login, register_on_tracker, get_topic_list, extract_topic_data,
    download_cover_image, human_delay, DomainBannedException,
)
from ..browser.tracker_profiles import get_profile, TrackerProfile
from ..browser.title_parser import parse_title, matches_format_filter
from ..api.telegram_captcha import TelegramCaptchaSolver
from ..api.notletters import NotLettersClient
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
        self.browser: WorkerBrowser | None = None
        self.ctx: BrowserContext | None = None
        self.page = None
        self.captcha_solver: TelegramCaptchaSolver | None = None
        self.notletters: NotLettersClient | None = None

        self._current_email_id: int | None = None
        self._current_page_num: int | None = None
        self._topic_queue: list[dict] = []

    # ── Account lifecycle ──────────────────────────────────────

    def _new_account_cycle(self, cfg: dict, profile: TrackerProfile) -> bool:
        """Take credentials → optionally register → log in."""
        skip_reg = cfg["tracker"].get("skip_registration", False)

        if self.ctx:
            try:
                self.ctx.close()
            except Exception:
                pass
            self.ctx = None
            self.page = None

        email = self.repo.get_fresh_email()
        if not email:
            self._emit_status("No fresh emails — add more in the Emails tab!")
            return False

        self._current_email_id = email.id
        self._emit_status(f"Using: {email.email}")
        self.ctx = self.browser.create_context(proxy=self.proxy)

        if skip_reg:
            username = email.email
            password = email.password
            self._emit_status(f"[{profile.name}] Logging in as {username} (skip registration)...")
        else:
            first_page = self.ctx.new_page()
            first_page.evaluate(f"document.title = 'Worker: {self.worker_id}'")

            username = _random_username()
            password = _random_password()

            self._emit_status(f"[{profile.name}] Registering {username} with {email.email}...")
            try:
                success = register_on_tracker(
                    self.ctx, profile, username, password, email.email,
                    captcha_solver=self.captcha_solver,
                    worker_id=self.worker_id,
                    status_callback=self._emit_status,
                    notletters_client=self.notletters,
                    email_password=email.password,
                )
            except DomainBannedException as e:
                first_page.close()
                log.warning(f"Domain '{e.domain}' blacklisted")
                self._emit_status(f"Domain '{e.domain}' blacklisted — banned")
                self.repo.add_blocked_domain(e.domain, reason=f"Blacklisted by {profile.name}")
                self.repo.mark_email_used(email.id)
                try:
                    self.ctx.close()
                except Exception:
                    pass
                self.ctx = None
                return False

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

        self._emit_status(f"[{profile.name}] Logging in as {username}...")
        try:
            self.page = login(self.ctx, profile, username, password)
        except Exception as e:
            self.log.error(f"Login failed: {e}")
            self._emit_status(f"Login failed: {e}")
            self.repo.mark_email_used(email.id)
            try:
                self.ctx.close()
            except Exception:
                pass
            self.ctx = None
            return False

        try:
            self.page.evaluate(f"document.title = '{self.worker_id} | {username} | ' + document.title")
        except Exception:
            pass

        self.repo.mark_email_used(email.id)
        self._emit_status(f"Ready: {username}")
        return True

    def _ensure_account(self, cfg: dict, profile: TrackerProfile) -> bool:
        if self.page is not None:
            return True
        while not self.should_stop:
            if self._new_account_cycle(cfg, profile):
                return True
            self._emit_status("Retrying in 15s...")
            self._stop_event.wait(timeout=15)
        return False

    # ── Main work loop ─────────────────────────────────────────

    def work(self):
        cfg = load_config()
        tracker_name = cfg["tracker"].get("profile", "rutracker")
        profile = get_profile(tracker_name)
        category_id = cfg["tracker"]["category_id"]
        format_filters = cfg["tags"].get("format_filters", cfg["tags"].get("download_tags", []))
        download_dir = get_download_dir(cfg)

        tg = cfg.get("telegram", {})
        if tg.get("bot_token") and tg.get("chat_id"):
            self.captcha_solver = TelegramCaptchaSolver(tg["bot_token"], tg["chat_id"])

        nl = cfg.get("notletters", {})
        if nl.get("api_token"):
            self.notletters = NotLettersClient(nl["api_token"])

        if not category_id:
            self._emit_status("No category ID configured")
            return

        self.browser = WorkerBrowser(self.worker_id)
        self.browser.start()

        try:
            self._run_parse_loop(cfg, profile, category_id, format_filters, download_dir)
        finally:
            if self.ctx:
                try:
                    self.ctx.close()
                except Exception:
                    pass
            self.browser.stop()

    def _run_parse_loop(self, cfg, profile, category_id, format_filters, download_dir):
        if not self._ensure_account(cfg, profile):
            return

        pages_start = cfg["tracker"]["pages_start"]
        pages_end = cfg["tracker"]["pages_end"]
        pages_total = max(0, pages_end - pages_start + 1)

        while not self.should_stop:
            self.wait_if_paused()
            if self.should_stop:
                break

            # Get next page if queue is empty
            if not self._topic_queue:
                if self._current_page_num is not None:
                    self.repo.complete_page(category_id, self._current_page_num, self.worker_id)
                    ps = getattr(self, "_page_stats", {})
                    self._emit_status(
                        f"Page {self._current_page_num}/{pages_end} done — "
                        f"saved: {ps.get('saved', 0)}, "
                        f"filtered: {ps.get('filtered', 0)}, "
                        f"dupes: {ps.get('duplicate', 0)}, "
                        f"errors: {ps.get('error', 0)}"
                    )

                self._current_page_num = self.repo.claim_next_page(category_id, self.worker_id)
                if self._current_page_num is None:
                    self._emit_status("All pages processed!")
                    break

                self._emit_status(f"Scanning page {self._current_page_num}/{pages_end}...")
                try:
                    topics = get_topic_list(self.page, profile, category_id, self._current_page_num)
                    self._topic_queue = list(topics)
                    self._page_stats = {"total": len(topics), "filtered": 0, "duplicate": 0, "saved": 0, "error": 0}
                    self._emit_status(f"Page {self._current_page_num}/{pages_end}: {len(topics)} topics found")
                except Exception as e:
                    self.log.error(f"Failed to scan page {self._current_page_num}: {e}")
                    self._emit_status(f"Scan error p.{self._current_page_num}: {e}")
                    self.repo.release_page(category_id, self.worker_id)
                    self._current_page_num = None
                    continue

            # Process next topic
            if self._topic_queue:
                topic = self._topic_queue.pop(0)
                self._process_topic(profile, topic, category_id, format_filters, download_dir)

        # Release unfinished page
        if self._current_page_num is not None and self._topic_queue:
            self.repo.release_page(category_id, self.worker_id)

    # ── Topic processing ───────────────────────────────────────

    def _process_topic(self, profile, topic, category_id, format_filters, download_dir):
        topic_id = topic["topic_id"]
        title = topic["title"]
        ps = getattr(self, "_page_stats", {})

        # Skip if already in DB
        if self.repo.torrent_exists(topic_id):
            ps["duplicate"] = ps.get("duplicate", 0) + 1
            return

        # 1. Parse title: [Studio]Name[Tags/Formats][Devices]
        parsed = parse_title(title)

        # 2. Check if formats match the filter
        if format_filters and not matches_format_filter(parsed["formats"], format_filters):
            ps["filtered"] = ps.get("filtered", 0) + 1
            log.debug(f"Topic {topic_id}: formats {parsed['formats']} don't match filter, skipping")
            return

        self._emit_status(f"Topic {topic_id}: {parsed['film_name'][:40]}...")

        # 3. Open topic page and extract all data
        try:
            details = extract_topic_data(self.page, profile, topic_id)
        except Exception as e:
            self.log.error(f"Failed to extract topic {topic_id}: {e}")
            ps["error"] = ps.get("error", 0) + 1
            return

        # 4. Download cover image
        cover = {"path": None, "data": None}
        if details["cover_url"]:
            cover = download_cover_image(self.page, details["cover_url"], download_dir, topic_id)

        # 5. Save everything to DB
        data = {
            "topic_id": topic_id,
            "title_raw": title,
            "category_id": category_id,
            "page_number": self._current_page_num or 0,
            "studio": parsed["studio"],
            "film_name": parsed["film_name"],
            "actors": parsed["actors"],
            "tags": parsed["tags"],
            "formats": parsed["formats"],
            "devices": parsed["devices"],
            "year": details["year"],
            "description": details["description"],
            "duration": details["duration"],
            "file_size": details["file_size"],
            "cover_url": details["cover_url"],
            "cover_path": cover["path"],
            "cover_data": cover["data"],
            "download_url": details["download_url"],
            "seeds": details["seeds"],
            "peers": details["peers"],
        }

        t = self.repo.save_torrent_data(data)
        if t:
            ps["saved"] = ps.get("saved", 0) + 1
            self._emit_status(
                f"Saved: {parsed['film_name'][:30]} | "
                f"formats={parsed['formats']} seeds={details['seeds']}"
            )

        human_delay(0.5, 1.5)
