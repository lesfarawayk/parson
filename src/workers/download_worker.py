"""
Download worker — logs in to tracker → downloads .torrent files → saves to DB.

Takes torrents that have download_url but no torrent_file yet.
Each worker runs its own Playwright browser.
"""

import logging
import time
from playwright.sync_api import BrowserContext

from .base_worker import BaseWorker
from ..db.repository import Repository
from ..browser.browser_manager import WorkerBrowser
from ..browser.tracker_actions import login, human_delay
from ..browser.tracker_profiles import get_profile, TrackerProfile
from ..config_manager import load_config

log = logging.getLogger(__name__)


def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec}s"
    h, mins = divmod(m, 60)
    return f"{h}h {mins}m"


class DownloadWorker(BaseWorker):
    def __init__(self, worker_id: str, repo: Repository, proxy: dict | None = None):
        super().__init__(worker_id, name=f"DL-{worker_id}")
        self.repo = repo
        self.proxy = proxy
        self.browser: WorkerBrowser | None = None
        self.ctx: BrowserContext | None = None
        self.page = None

        self.started_at: float | None = None
        self.total_stats = {"downloaded": 0, "errors": 0, "skipped": 0}

    def work(self):
        self.started_at = time.monotonic()
        cfg = load_config()
        tracker_name = cfg["tracker"].get("profile", "rutracker")
        profile = get_profile(tracker_name)
        dl_cfg = cfg.get("downloader", {})

        # Get email credentials for this worker
        share = dl_cfg.get("share_email", True)
        if share:
            email = self.repo.get_first_email()
        else:
            email = self.repo.get_fresh_email()

        if not email:
            self._emit_status("No emails available!")
            return

        self.browser = WorkerBrowser(self.worker_id)
        self.browser.start()

        try:
            self.ctx = self.browser.create_context(proxy=self.proxy)
            username = email.email
            password = email.password

            self._emit_status(f"Logging in as {username}...")
            try:
                self.page = login(self.ctx, profile, username, password)
            except Exception as e:
                self.log.error(f"Login failed: {e}")
                self._emit_status(f"Login failed: {e}")
                if not share:
                    self.repo.mark_email_used(email.id)
                return

            self._emit_status(f"Ready: {username}")

            # Diagnostic: check how many torrents are available
            dl_stats = self.repo.get_download_stats()
            self.log.info(f"Download stats: {dl_stats}")
            self._emit_status(
                f"Ready: {username} | "
                f"Pending: {dl_stats['pending']}, "
                f"Already downloaded: {dl_stats['downloaded']}, "
                f"Total with URL: {dl_stats['total_with_url']}"
            )

            if dl_stats["pending"] == 0 and dl_stats["downloading"] == 0:
                self._emit_status("Nothing to download — all torrents already have .torrent files or no download URLs")
                return

            max_per_account = dl_cfg.get("max_per_account", 50)
            self._download_loop(profile, max_per_account)
        finally:
            if self.ctx:
                try:
                    self.ctx.close()
                except Exception:
                    pass
            self.browser.stop()

            elapsed = time.monotonic() - (self.started_at or time.monotonic())
            ts = self.total_stats
            self.log.info(
                f"=== DL FINISHED === downloaded: {ts['downloaded']}, "
                f"errors: {ts['errors']}, time: {_fmt_duration(elapsed)}"
            )

    def _download_loop(self, profile: TrackerProfile, max_downloads: int):
        count = 0
        while not self.should_stop and count < max_downloads:
            self.wait_if_paused()
            if self.should_stop:
                break

            item = self.repo.claim_torrent_for_download(self.worker_id)
            if not item:
                self.log.info("claim_torrent_for_download returned None — no more items")
                self._emit_status("No more torrents to download — done!")
                break

            topic_id = item["topic_id"]
            name = item["film_name"][:40] or item["title_raw"][:40]
            dl_url = item["download_url"]

            self._emit_status(f"Downloading [{topic_id}] {name}...")
            self.log.info(f"Downloading torrent {topic_id}: {dl_url}")

            try:
                torrent_bytes = self._download_torrent(profile, topic_id, dl_url)
                if torrent_bytes and len(torrent_bytes) > 50:
                    self.repo.save_torrent_file(topic_id, torrent_bytes)
                    self.total_stats["downloaded"] += 1
                    count += 1
                    size_kb = len(torrent_bytes) / 1024
                    self._emit_status(
                        f"Saved [{topic_id}] {name} ({size_kb:.0f} KB) "
                        f"| total: {self.total_stats['downloaded']}"
                    )
                    self.log.info(f"Saved torrent {topic_id}: {size_kb:.0f} KB")
                else:
                    self.log.warning(f"Torrent {topic_id}: empty or too small response")
                    self.repo.mark_torrent_error(topic_id)
                    self.total_stats["errors"] += 1
                    self._emit_status(f"Error [{topic_id}]: empty response")
            except Exception as e:
                self.log.error(f"Failed to download torrent {topic_id}: {e}")
                self.repo.mark_torrent_error(topic_id)
                self.total_stats["errors"] += 1
                self._emit_status(f"Error [{topic_id}]: {e}")

            human_delay(1.0, 3.0)

        elapsed = time.monotonic() - (self.started_at or time.monotonic())
        self._emit_status(
            f"Done — {self.total_stats['downloaded']} downloaded, "
            f"{self.total_stats['errors']} errors | {_fmt_duration(elapsed)}"
        )

    def _download_torrent(self, profile: TrackerProfile, topic_id: str, dl_url: str) -> bytes | None:
        """Download .torrent file via the browser session (uses cookies)."""
        # Build full URL if relative
        if dl_url.startswith("/"):
            url = profile.base_url + dl_url
        elif not dl_url.startswith("http"):
            url = profile.base_url + "/" + dl_url
        else:
            url = dl_url

        self.log.info(f"GET {url}")
        response = self.page.request.get(url)

        if not response.ok:
            self.log.warning(f"Torrent {topic_id}: HTTP {response.status} from {url}")
            return None

        content_type = response.headers.get("content-type", "")
        body = response.body()

        self.log.info(
            f"Torrent {topic_id}: status={response.status}, "
            f"content-type={content_type}, size={len(body)} bytes"
        )

        # Check if we got HTML instead of torrent (login wall, etc.)
        if b"<html" in body[:200].lower() or b"<!doctype" in body[:200].lower():
            self.log.warning(f"Torrent {topic_id}: got HTML instead of .torrent (login expired?)")
            return None

        return body
