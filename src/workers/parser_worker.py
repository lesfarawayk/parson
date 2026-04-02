"""
Parser worker — scans tracker category pages, extracts tags, downloads matching torrents.

State machine per worker:
  NEED_LOGIN → LOGIN → SCAN_PAGE → PROCESS_TOPIC → DOWNLOAD → (repeat)
                                                        ↓
                                              NEED_RELOGIN (limit hit)
                                                        ↓
                                                    LOGIN (new account, same topic queue)

Key design:
- Page number and topic queue are stored in worker memory (Python state)
- Account switch only replaces browser context + session, NOT the topic queue
- After re-login, worker continues from the exact topic where it stopped
- Page claiming is coordinated via DB (thread-safe)
"""

import logging
from enum import Enum, auto
from pathlib import Path
from playwright.sync_api import BrowserContext

from .base_worker import BaseWorker
from ..db.repository import Repository
from ..browser.browser_manager import BrowserManager
from ..browser.tracker_actions import (
    login, get_topic_list, extract_topic_details,
    download_torrent_file, download_cover_image, human_delay,
)
from ..config_manager import load_config, get_download_dir

log = logging.getLogger(__name__)


class ParserState(Enum):
    NEED_LOGIN = auto()
    SCANNING = auto()
    PROCESSING = auto()
    NEED_RELOGIN = auto()
    DONE = auto()


class ParserWorker(BaseWorker):
    def __init__(self, worker_id: str, browser_manager: BrowserManager, repo: Repository,
                 proxy: dict | None = None):
        super().__init__(worker_id, name=f"Parser-{worker_id}")
        self.browser = browser_manager
        self.repo = repo
        self.proxy = proxy
        self.ctx: BrowserContext | None = None
        self.page = None
        self.current_account = None
        self.downloads_remaining = 0

        # Current work state — survives account switches
        self._current_page_num: int | None = None
        self._topic_queue: list[dict] = []  # topics left to process on current page
        self._pending_download: dict | None = None  # topic waiting for download after relogin

    # ── Account & Login ─────────────────────────────────────────

    def _acquire_account(self) -> bool:
        """Get a fresh tracker account from DB. Returns True if got one."""
        account = self.repo.get_fresh_tracker_account()
        if not account:
            return False
        self.current_account = account
        self.downloads_remaining = account.max_downloads - account.downloads_count
        return True

    def _login(self, cfg: dict) -> bool:
        """
        Create new browser context and log in.
        Does NOT touch _topic_queue or _current_page_num — state is preserved.
        Returns True on success.
        """
        # Close old context
        if self.ctx:
            try:
                self.ctx.close()
            except Exception:
                pass
            self.ctx = None
            self.page = None

        self.ctx = self.browser.create_context(proxy=self.proxy)

        try:
            self.page = login(
                self.ctx,
                cfg["tracker"]["base_url"],
                self.current_account.username,
                self.current_account.password,
            )
            self._emit_status(f"Logged in as {self.current_account.username} ({self.downloads_remaining} downloads left)")
            return True
        except Exception as e:
            self.log.error(f"Login failed for {self.current_account.username}: {e}")
            self.repo.exhaust_tracker_account(self.current_account.id)
            self.current_account = None
            self._emit_status(f"Login failed: {e}")
            return False

    def _ensure_logged_in(self, cfg: dict) -> bool:
        """
        Make sure we have a working session.
        Tries to get account + login in a loop until success or stop.
        """
        while not self.should_stop:
            if self.current_account is None:
                self._emit_status("Getting fresh account...")
                if not self._acquire_account():
                    self._emit_status("No accounts available — waiting...")
                    self._stop_event.wait(timeout=10)
                    continue

            if self.page is not None:
                return True  # already logged in

            self._emit_status(f"Logging in as {self.current_account.username}...")
            if self._login(cfg):
                return True
            # Login failed — account was marked exhausted, loop will try next
        return False

    def _switch_account(self, cfg: dict) -> bool:
        """
        Switch to a new account. Called when download limit is hit.
        Preserves _topic_queue and _current_page_num.
        """
        self._emit_status("Download limit reached — switching account...")
        if self.current_account:
            self.repo.exhaust_tracker_account(self.current_account.id)
        self.current_account = None
        self.page = None  # force re-login in _ensure_logged_in
        return self._ensure_logged_in(cfg)

    # ── Main work loop ──────────────────────────────────────────

    def work(self):
        cfg = load_config()
        category_id = cfg["tracker"]["category_id"]
        download_tags = cfg["tags"]["download_tags"]
        record_tags = cfg["tags"]["record_tags"]
        download_dir = get_download_dir(cfg)

        if not category_id:
            self._emit_status("No category ID configured")
            return

        # Initial login
        if not self._ensure_logged_in(cfg):
            return

        # If we had a pending download from before relogin, do it now
        # (this handles the edge case of restart)

        state = ParserState.SCANNING

        while not self.should_stop:
            self.wait_if_paused()
            if self.should_stop:
                break

            # ── Handle pending download after account switch ────
            if self._pending_download and state != ParserState.NEED_RELOGIN:
                topic = self._pending_download
                self._pending_download = None
                dl_ok = self._do_download(cfg, topic["topic_id"], topic["cover_url"], download_dir)
                if not dl_ok:
                    # Need another account switch
                    if not self._switch_account(cfg):
                        break
                    self._pending_download = topic  # retry after next login
                    continue
                continue  # back to processing remaining topics

            # ── Get next page if queue is empty ─────────────────
            if not self._topic_queue:
                # Complete previous page if any
                if self._current_page_num is not None:
                    self.repo.complete_page(category_id, self._current_page_num, self.worker_id)
                    self._emit_status(f"Page {self._current_page_num} completed")

                # Claim next page
                self._current_page_num = self.repo.claim_next_page(category_id, self.worker_id)
                if self._current_page_num is None:
                    self._emit_status("All pages processed")
                    break

                self._emit_status(f"Scanning page {self._current_page_num}...")
                try:
                    topics = get_topic_list(
                        self.page,
                        cfg["tracker"]["forum_url"],
                        category_id,
                        self._current_page_num,
                    )
                    self._topic_queue = list(topics)
                    self._emit_status(f"Page {self._current_page_num}: {len(topics)} topics")
                except Exception as e:
                    self.log.error(f"Failed to scan page {self._current_page_num}: {e}")
                    self._emit_status(f"Scan error: {e}")
                    self.repo.release_page(category_id, self.worker_id)
                    self._current_page_num = None
                    continue

            # ── Process next topic from queue ───────────────────
            if self._topic_queue:
                topic = self._topic_queue.pop(0)
                self._process_single_topic(cfg, topic, category_id, download_tags, record_tags, download_dir)

        # Cleanup
        if self._current_page_num is not None and self._topic_queue:
            # Didn't finish this page — release it so another worker can take it
            self.repo.release_page(category_id, self.worker_id)

        if self.ctx:
            try:
                self.ctx.close()
            except Exception:
                pass

    # ── Single topic processing ─────────────────────────────────

    def _process_single_topic(self, cfg, topic, category_id, download_tags, record_tags, download_dir):
        """Process one topic: extract details, optionally download."""
        topic_id = topic["topic_id"]
        title = topic["title"]

        # Skip duplicates
        t = self.repo.add_torrent(topic_id, title, category_id, self._current_page_num)
        if t is None:
            return  # already in DB

        self._emit_status(f"Topic {topic_id}: {title[:50]}...")

        try:
            details = extract_topic_details(
                self.page,
                cfg["tracker"]["base_url"],
                topic_id,
                download_tags,
                record_tags,
            )
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

        # Download if matching tags
        if details["matched_download_tags"]:
            if self.downloads_remaining <= 0:
                # Save topic for after relogin
                self._pending_download = {
                    "topic_id": topic_id,
                    "cover_url": details["cover_url"],
                }
                if not self._switch_account(cfg):
                    return
                # Will be handled in main loop via _pending_download
                return

            self._do_download(cfg, topic_id, details["cover_url"], download_dir)

        human_delay(0.5, 1.5)

    def _do_download(self, cfg, topic_id, cover_url, download_dir) -> bool:
        """
        Download a torrent file. Returns True on success, False if limit hit.
        """
        if self.downloads_remaining <= 0:
            return False

        self.repo.mark_torrent_downloading(topic_id)
        self._emit_status(f"Downloading {topic_id}...")

        file_path = download_torrent_file(self.page, cfg["tracker"]["base_url"], topic_id, download_dir)
        if file_path:
            cover_path = download_cover_image(self.page, cover_url, download_dir, topic_id)
            self.repo.mark_torrent_downloaded(topic_id, file_path, cover_path)

            self.downloads_remaining -= 1
            self.repo.increment_download(self.current_account.id)
            self._emit_status(f"Downloaded {topic_id} ({self.downloads_remaining} left)")
            return True
        else:
            self.repo.mark_torrent_error(topic_id)
            self._emit_status(f"Download failed: {topic_id}")
            return True  # failed but not because of limit
