"""
Download worker — logs in to tracker → downloads .torrent files → saves to DB.

Takes torrents that have download_url but no torrent_file yet.
Each worker runs its own Playwright browser.
"""

import logging
import time
import re
from pathlib import Path
from playwright.sync_api import BrowserContext

from .base_worker import BaseWorker
from ..db.repository import Repository
from ..browser.browser_manager import WorkerBrowser
from ..browser.tracker_actions import login, human_delay
from ..browser.tracker_profiles import get_profile, TrackerProfile
from ..config_manager import load_config

log = logging.getLogger(__name__)

# Patterns that indicate rate limiting / download cap
RATE_LIMIT_PATTERNS = [
    r"лимит",
    r"limit",
    r"превыш",          # превышен
    r"подождите",
    r"wait",
    r"too many",
    r"try again",
    r"повтор",
    r"ограничен",       # ограничение
    r"quota",
    r"капча",
    r"captcha",
    r"скач.*огранич",   # скачивание ограничено
]
_RATE_RE = re.compile("|".join(RATE_LIMIT_PATTERNS), re.IGNORECASE)


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
        self._consecutive_errors = 0

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

            self._emit_status(f"Logged in: {username}")

            # Diagnostic: check how many torrents are available
            dl_stats = self.repo.get_download_stats()
            self.log.info(f"Download stats: {dl_stats}")
            self._emit_status(
                f"Ready: {username} | "
                f"Pending: {dl_stats['pending']}, "
                f"Downloaded: {dl_stats['downloaded']}, "
                f"Total with URL: {dl_stats['total_with_url']}"
            )

            if dl_stats["pending"] == 0 and dl_stats["downloading"] == 0:
                self._emit_status("Nothing to download — no pending torrents with download URLs")
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
                f"errors: {ts['errors']}, skipped: {ts['skipped']}, "
                f"time: {_fmt_duration(elapsed)}"
            )

    def _download_loop(self, profile: TrackerProfile, max_downloads: int):
        count = 0
        while not self.should_stop and count < max_downloads:
            self.wait_if_paused()
            if self.should_stop:
                break

            item = self.repo.claim_torrent_for_download(self.worker_id)
            if not item:
                self.log.info("No more items to claim — queue empty")
                self._emit_status("No more torrents to download — done!")
                break

            topic_id = item["topic_id"]
            name = item["film_name"][:40] or item["title_raw"][:40]
            dl_url = item["download_url"]

            self._emit_status(f"[{count+1}/{max_downloads}] Downloading [{topic_id}] {name}...")
            self.log.info(f"--- Downloading torrent {topic_id}: {dl_url} ---")

            result = self._download_with_retry(profile, topic_id, dl_url, name)

            if result == "ok":
                count += 1
                self._consecutive_errors = 0
            elif result == "rate_limited":
                # Rate limited — stop this worker, don't mark more as error
                self._emit_status(
                    f"Rate limited after {count} downloads. "
                    f"Stopping. (downloaded: {self.total_stats['downloaded']})"
                )
                self.log.warning(f"Rate limited — stopping worker after {count} downloads")
                break
            elif result == "error":
                self._consecutive_errors += 1
                if self._consecutive_errors >= 5:
                    self._emit_status(
                        f"5 consecutive errors — likely rate limited. "
                        f"Stopping. (downloaded: {self.total_stats['downloaded']})"
                    )
                    self.log.warning("5 consecutive errors — stopping worker")
                    break
            # else "skipped" — continue

            # Delay between downloads (longer after errors)
            if self._consecutive_errors > 0:
                wait = min(30 * self._consecutive_errors, 120)
                self._emit_status(f"Waiting {wait}s after error...")
                self.log.info(f"Error cooldown: {wait}s")
                for _ in range(wait):
                    if self.should_stop:
                        break
                    time.sleep(1)
            else:
                # Normal delay — be polite to tracker
                human_delay(3.0, 6.0)

        elapsed = time.monotonic() - (self.started_at or time.monotonic())
        ts = self.total_stats
        self._emit_status(
            f"Done — {ts['downloaded']} downloaded, "
            f"{ts['errors']} errors, {ts['skipped']} skipped | {_fmt_duration(elapsed)}"
        )

    def _download_with_retry(self, profile, topic_id, dl_url, name, max_retries=2):
        """
        Try to download a torrent. Returns:
          "ok"           — saved successfully
          "rate_limited" — detected rate limit, should stop
          "error"        — failed, marked as error
          "skipped"      — empty/invalid, skipped
        """
        for attempt in range(1, max_retries + 1):
            try:
                result = self._download_torrent(profile, topic_id, dl_url)
            except Exception as e:
                self.log.error(f"[{topic_id}] attempt {attempt} exception: {e}", exc_info=True)
                self._emit_status(f"Error [{topic_id}] attempt {attempt}: {e}")
                if attempt < max_retries:
                    wait = 15 * attempt
                    self.log.info(f"[{topic_id}] retrying in {wait}s...")
                    self._emit_status(f"Retrying [{topic_id}] in {wait}s...")
                    time.sleep(wait)
                    continue
                self.repo.mark_torrent_error(topic_id)
                self.total_stats["errors"] += 1
                return "error"

            if result["status"] == "rate_limited":
                # Put torrent back to queue (not error)
                self.repo.update_torrent_status(topic_id, "parsed")
                self.total_stats["skipped"] += 1
                return "rate_limited"

            if result["status"] == "html_response":
                # Got HTML — could be login expired or rate limit
                body_text = result.get("body_text", "")
                if _RATE_RE.search(body_text):
                    self.log.warning(f"[{topic_id}] Rate limit detected in HTML response")
                    self.repo.update_torrent_status(topic_id, "parsed")
                    self.total_stats["skipped"] += 1
                    return "rate_limited"
                # Unknown HTML — maybe login expired, retry once
                if attempt < max_retries:
                    self.log.warning(f"[{topic_id}] Got HTML, retrying in 10s...")
                    self._emit_status(f"[{topic_id}] Got HTML page, retrying...")
                    time.sleep(10)
                    continue
                self.log.error(f"[{topic_id}] Got HTML after {max_retries} attempts")
                self.repo.mark_torrent_error(topic_id)
                self.total_stats["errors"] += 1
                return "error"

            if result["status"] == "http_error":
                http_code = result.get("http_code", 0)
                if http_code == 429:
                    self.repo.update_torrent_status(topic_id, "parsed")
                    self.total_stats["skipped"] += 1
                    return "rate_limited"
                if attempt < max_retries:
                    wait = 15 * attempt
                    self.log.warning(f"[{topic_id}] HTTP {http_code}, retry in {wait}s...")
                    time.sleep(wait)
                    continue
                self.repo.mark_torrent_error(topic_id)
                self.total_stats["errors"] += 1
                return "error"

            if result["status"] == "empty":
                self.log.warning(f"[{topic_id}] Empty or too small response ({result.get('size', 0)} bytes)")
                self.repo.mark_torrent_error(topic_id)
                self.total_stats["errors"] += 1
                return "skipped"

            if result["status"] == "ok":
                data = result["data"]
                self.repo.save_torrent_file(topic_id, data)
                self.total_stats["downloaded"] += 1
                size_kb = len(data) / 1024
                self._emit_status(
                    f"Saved [{topic_id}] {name} ({size_kb:.1f} KB) "
                    f"| total: {self.total_stats['downloaded']}"
                )
                self.log.info(f"[{topic_id}] Saved: {size_kb:.1f} KB")
                return "ok"

        # Shouldn't reach here
        return "error"

    def _download_torrent(self, profile: TrackerProfile, topic_id: str, dl_url: str) -> dict:
        """
        Download .torrent file via the browser session (uses cookies).
        Returns a dict with status and details for the caller to decide.
        """
        # Build full URL if relative
        if dl_url.startswith("/"):
            url = profile.base_url + dl_url
        elif not dl_url.startswith("http"):
            url = profile.base_url + "/" + dl_url
        else:
            url = dl_url

        self.log.info(f"[{topic_id}] GET {url}")
        t0 = time.monotonic()
        response = self.page.request.get(url)
        elapsed = time.monotonic() - t0

        status_code = response.status
        content_type = response.headers.get("content-type", "")
        headers_str = ", ".join(f"{k}: {v}" for k, v in response.headers.items())

        self.log.info(
            f"[{topic_id}] Response: HTTP {status_code} in {elapsed:.1f}s | "
            f"content-type={content_type}"
        )
        self.log.debug(f"[{topic_id}] All headers: {headers_str}")

        if not response.ok:
            # Try to read body for diagnostics
            try:
                body = response.body()
                body_preview = body[:1000].decode("utf-8", errors="replace")
            except Exception:
                body_preview = "(could not read body)"
            self.log.warning(
                f"[{topic_id}] HTTP {status_code} error\n"
                f"  URL: {url}\n"
                f"  Content-Type: {content_type}\n"
                f"  Body preview: {body_preview}"
            )
            self._emit_status(f"HTTP {status_code} for [{topic_id}]")
            return {"status": "http_error", "http_code": status_code, "body_preview": body_preview}

        body = response.body()
        body_size = len(body)

        self.log.info(
            f"[{topic_id}] Body: {body_size} bytes | content-type: {content_type}"
        )

        # Check if we got HTML instead of torrent
        body_start = body[:500].lower()
        if b"<html" in body_start or b"<!doctype" in body_start:
            body_text = body[:3000].decode("utf-8", errors="replace")
            self.log.warning(
                f"[{topic_id}] Got HTML instead of .torrent!\n"
                f"  URL: {url}\n"
                f"  Content-Type: {content_type}\n"
                f"  Body size: {body_size}\n"
                f"  Body text:\n{body_text}"
            )
            self._emit_status(f"HTML response for [{topic_id}] (not .torrent)")

            # Try to take screenshot for debugging
            self._save_debug_screenshot(topic_id, "html_response")

            return {"status": "html_response", "body_text": body_text}

        # Check for rate limit even in non-HTML responses
        if status_code == 429:
            self.log.warning(f"[{topic_id}] HTTP 429 Too Many Requests")
            return {"status": "rate_limited"}

        # Validate minimum size (real .torrent files are > 50 bytes)
        if body_size < 50:
            self.log.warning(
                f"[{topic_id}] Response too small: {body_size} bytes\n"
                f"  Body hex: {body[:50].hex()}"
            )
            return {"status": "empty", "size": body_size}

        # Looks good — return the data
        self.log.info(
            f"[{topic_id}] OK — valid torrent data, {body_size} bytes"
        )
        return {"status": "ok", "data": body}

    def _save_debug_screenshot(self, topic_id: str, reason: str):
        """Take a screenshot of the current page for debugging."""
        try:
            debug_dir = Path("data/logs/debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            path = debug_dir / f"dl_{topic_id}_{reason}_{ts}.png"
            self.page.screenshot(path=str(path))
            self.log.info(f"[{topic_id}] Debug screenshot saved: {path}")
        except Exception as e:
            self.log.debug(f"[{topic_id}] Could not save screenshot: {e}")
