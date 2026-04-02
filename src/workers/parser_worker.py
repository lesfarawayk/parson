"""
Parser worker — scans tracker category pages, extracts tags, downloads matching torrents.

Lifecycle per page:
1. Claim next unclaimed page from DB
2. Login to tracker (or reuse session)
3. Open category page, extract topic list
4. For each topic: open topic, extract tags/description/cover
5. If download tags match: download .torrent file
6. If download limit reached: get new account, re-login
7. Mark page completed, repeat
"""

import logging
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

    def _get_account_and_login(self, cfg: dict) -> bool:
        """Get a fresh tracker account from DB and log in. Returns True on success."""
        account = self.repo.get_fresh_tracker_account()
        if not account:
            self._emit_status("No tracker accounts available — waiting...")
            return False

        self.current_account = account
        self.downloads_remaining = account.max_downloads - account.downloads_count

        # Close old context, create new one
        if self.ctx:
            try:
                self.ctx.close()
            except Exception:
                pass

        self.ctx = self.browser.create_context(proxy=self.proxy)
        try:
            self.page = login(
                self.ctx,
                cfg["tracker"]["base_url"],
                account.username,
                account.password,
            )
            self._emit_status(f"Logged in as {account.username}")
            return True
        except Exception as e:
            self.log.error(f"Login failed for {account.username}: {e}")
            self.repo.exhaust_tracker_account(account.id)
            self._emit_status(f"Login failed: {e}")
            return False

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
        while not self.should_stop:
            if self._get_account_and_login(cfg):
                break
            # Wait and retry
            self._stop_event.wait(timeout=10)

        # Main loop: process pages
        while not self.should_stop:
            self.wait_if_paused()
            if self.should_stop:
                break

            # Claim next page
            page_num = self.repo.claim_next_page(category_id, self.worker_id)
            if page_num is None:
                self._emit_status("All pages processed")
                break

            self._emit_status(f"Processing page {page_num}")
            try:
                self._process_page(cfg, category_id, page_num, download_tags, record_tags, download_dir)
                self.repo.complete_page(category_id, page_num, self.worker_id)
                self._emit_status(f"Page {page_num} completed")
            except Exception as e:
                self.log.error(f"Error on page {page_num}: {e}")
                self._emit_status(f"Error on page {page_num}: {e}")
                self.repo.release_page(category_id, self.worker_id)

        # Cleanup
        if self.ctx:
            try:
                self.ctx.close()
            except Exception:
                pass

    def _process_page(self, cfg, category_id, page_num, download_tags, record_tags, download_dir):
        """Process all topics on a single page."""
        topics = get_topic_list(
            self.page,
            cfg["tracker"]["forum_url"],
            category_id,
            page_num,
        )

        for topic in topics:
            if self.should_stop:
                break
            self.wait_if_paused()

            topic_id = topic["topic_id"]
            title = topic["title"]

            # Add to DB (skip duplicates)
            t = self.repo.add_torrent(topic_id, title, category_id, page_num)
            if t is None:
                continue  # already processed

            self._emit_status(f"Checking topic {topic_id}: {title[:40]}...")

            # Extract details
            details = extract_topic_details(
                self.page,
                cfg["tracker"]["base_url"],
                topic_id,
                download_tags,
                record_tags,
            )

            self.repo.update_torrent_tags(
                topic_id,
                details["matched_download_tags"],
                details["matched_record_tags"],
                details["description"],
                details["cover_url"],
            )

            # Download if matching download tags found
            if details["matched_download_tags"]:
                self._download_topic(cfg, topic_id, details["cover_url"], download_dir)

            human_delay(0.5, 1.5)

    def _download_topic(self, cfg, topic_id, cover_url, download_dir):
        """Download torrent file, re-login if download limit reached."""
        # Check if we need new account
        if self.downloads_remaining <= 0:
            self._emit_status("Download limit reached — switching account...")
            if self.current_account:
                self.repo.exhaust_tracker_account(self.current_account.id)
            while not self.should_stop:
                if self._get_account_and_login(cfg):
                    break
                self._stop_event.wait(timeout=10)
            if self.should_stop:
                return

        self.repo.mark_torrent_downloading(topic_id)
        self._emit_status(f"Downloading torrent {topic_id}...")

        file_path = download_torrent_file(self.page, cfg["tracker"]["base_url"], topic_id, download_dir)
        if file_path:
            # Download cover image too
            cover_path = download_cover_image(self.page, cover_url, download_dir, topic_id)
            self.repo.mark_torrent_downloaded(topic_id, file_path, cover_path)

            # Track download count
            still_ok = self.repo.increment_download(self.current_account.id)
            self.downloads_remaining -= 1
            self._emit_status(f"Downloaded {topic_id} ({self.downloads_remaining} remaining)")
        else:
            self.repo.mark_torrent_error(topic_id)
            self._emit_status(f"Failed to download {topic_id}")
