"""Parser worker — scans category pages, extracts tags, downloads torrents."""

import re
import logging
import threading
from typing import Optional

from src.config.settings import AppConfig
from src.db.manager import DatabaseManager
from src.browser.tracker import TrackerBrowser
from src.workers.signals import WorkerSignals

logger = logging.getLogger(__name__)


class ParserWorker(threading.Thread):
    """Worker thread that processes category pages one at a time.

    Flow:
    1. Claim next page from DB
    2. Login with available account
    3. Scan page for torrent topics
    4. For each topic: check tags, save to DB
    5. If download trigger tag found and account has quota: download
    6. If account exhausted (3-4 downloads): re-login with new account
    7. Mark page complete, claim next
    """

    def __init__(self, worker_id: str, config: AppConfig, db: DatabaseManager,
                 signals: WorkerSignals):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self.config = config
        self.db = db
        self.signals = signals
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused by default

        self._browser: Optional[TrackerBrowser] = None
        self._current_account = None
        self._downloads_this_session = 0

    def stop(self):
        self._stop_event.set()

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def _emit_log(self, msg: str):
        self.signals.log.emit(self.worker_id, msg)
        logger.info(f"[{self.worker_id}] {msg}")

    def _emit_status(self, status: str):
        self.signals.status.emit(self.worker_id, status)

    def _check_stop(self) -> bool:
        return self._stop_event.is_set()

    def _wait_if_paused(self):
        self._pause_event.wait()

    def run(self):
        self._emit_log("Parser worker starting...")
        self._emit_status("starting")

        try:
            # Get proxy
            proxy = self.db.get_proxy()
            self._browser = TrackerBrowser(self.worker_id, self.config, proxy)
            self._browser.start()
            self._emit_log(f"Browser started (proxy: {proxy or 'none'})")

            # Login
            if not self._ensure_logged_in():
                self._emit_log("No accounts available. Waiting...")
                self._emit_status("waiting_account")
                # Will retry in the main loop
                pass

            # Main loop: process pages
            while not self._check_stop():
                self._wait_if_paused()
                if self._check_stop():
                    break

                # Claim a page
                page_num = self.db.claim_next_page(self.worker_id)
                if page_num is None:
                    self._emit_log("No more pages to process. Done!")
                    self._emit_status("done")
                    break

                self._emit_log(f"Processing page {page_num}...")
                self._emit_status(f"page_{page_num}")
                self.db.update_worker_state(
                    self.worker_id, 'parser',
                    status='working', current_page=page_num
                )

                try:
                    self._process_page(page_num)
                except Exception as e:
                    self._emit_log(f"Error on page {page_num}: {e}")
                    self.signals.error.emit(self.worker_id, str(e))
                    self.db.fail_page(page_num, str(e))

        except Exception as e:
            self._emit_log(f"Fatal error: {e}")
            self.signals.error.emit(self.worker_id, str(e))
        finally:
            if self._browser:
                self._browser.stop()
            if self._current_account:
                self.db.release_account(self._current_account.id)
            self._emit_status("stopped")
            self.signals.finished.emit(self.worker_id)

    def _ensure_logged_in(self) -> bool:
        """Ensure we have a valid account and are logged in."""
        if self._current_account:
            return True

        max_dl = self.config.tracker.max_downloads_per_account
        account = self.db.get_available_account(self.worker_id, max_dl)
        if not account:
            return False

        self._current_account = account
        self._downloads_this_session = 0
        success = self._browser.login(account.username, account.password)
        if success:
            self._emit_log(f"Logged in as {account.username}")
            return True
        else:
            self._emit_log(f"Login failed for {account.username}")
            self.db.release_account(account.id)
            self._current_account = None
            return False

    def _switch_account(self) -> bool:
        """Logout, release current account, get new one, login."""
        self._emit_log("Switching account (download limit reached)...")
        self._emit_status("switching_account")

        if self._current_account:
            self._browser.logout()
            self.db.release_account(self._current_account.id)
            self._current_account = None

        return self._ensure_logged_in()

    def _process_page(self, page_number: int):
        """Process all torrents on a single category page."""
        topics = self._browser.get_category_page(page_number)
        if not topics:
            self._emit_log(f"Page {page_number}: no topics found")
            self.db.complete_page(page_number, 0, 0)
            return

        torrents_found = len(topics)
        torrents_downloaded = 0

        for i, topic in enumerate(topics):
            if self._check_stop():
                self.db.fail_page(page_number, "Worker stopped")
                return
            self._wait_if_paused()

            tracker_id = topic['tracker_id']
            title = topic['title']
            page_url = topic['page_url']

            self._emit_log(f"  [{i+1}/{len(topics)}] {title[:60]}...")

            # Check if already processed
            if self.db.is_torrent_known(tracker_id):
                self._emit_log(f"  Already known, skipping")
                continue

            # Get details
            details = self._browser.get_torrent_details(page_url)
            if not details:
                continue

            description = details.get('description', '')
            cover_url = details.get('cover_url')

            # Analyze tags
            found_tags = self._extract_tags(title, description)
            should_download = any(
                t[1] == 'download_trigger' for t in found_tags
            )

            # Save cover
            cover_path = None
            if cover_url:
                covers_dir = f"{self.config.download_dir}/covers"
                cover_path = self._browser.save_cover_image(
                    cover_url, covers_dir, tracker_id
                )

            # Save to DB
            torrent_data = {
                'tracker_id': tracker_id,
                'title': title,
                'description': description,
                'cover_url': cover_url,
                'cover_path': cover_path,
                'page_url': page_url,
                'category_page': page_number,
                'should_download': should_download,
                'tags': found_tags,
            }

            # Download if tag match and we have account quota
            if should_download:
                if not self._ensure_logged_in():
                    self._emit_log("  No account available for download, saving without file")
                    self.db.save_torrent(torrent_data)
                    continue

                dl_dir = f"{self.config.download_dir}/torrents"
                filepath = self._browser.download_torrent(page_url, dl_dir)
                if filepath:
                    torrent_data['torrent_file_path'] = filepath
                    torrent_data['is_downloaded'] = True
                    torrent_data['downloaded_by'] = self._current_account.username
                    torrents_downloaded += 1

                    # Track downloads on this account
                    self.db.increment_download(
                        self._current_account.id,
                        self.config.tracker.max_downloads_per_account
                    )
                    self._downloads_this_session += 1

                    # Check if need to switch account
                    if self._downloads_this_session >= self.config.tracker.max_downloads_per_account:
                        if not self._switch_account():
                            self._emit_log("  No more accounts, continuing without downloads")
            else:
                self.db.save_torrent(torrent_data)

            torrent_data['is_processed'] = True
            self.db.save_torrent(torrent_data)

        self.db.complete_page(page_number, torrents_found, torrents_downloaded)
        self._emit_log(f"Page {page_number} done: {torrents_found} found, {torrents_downloaded} downloaded")

    def _extract_tags(self, title: str, description: str):
        """Check title and description for configured tags.
        Returns list of (tag_name, tag_type) tuples."""
        text = f"{title} {description}".lower()
        found = []

        # Check download trigger tags
        for tag in self.config.tags.download_triggers:
            if tag.lower() in text:
                found.append((tag, 'download_trigger'))

        # Check record tags
        for tag in self.config.tags.record_tags:
            if tag.lower() in text:
                found.append((tag, 'record'))

        return found
