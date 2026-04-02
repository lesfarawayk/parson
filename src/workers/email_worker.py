"""
Email registration worker — creates Rambler email accounts and stores them in the DB.

Runs in a loop, creating accounts as needed to maintain a pool of fresh emails.
"""

import logging
from ..browser.browser_manager import BrowserManager
from ..browser.rambler_actions import register_rambler_email
from ..db.repository import Repository
from ..config_manager import load_config
from .base_worker import BaseWorker

log = logging.getLogger(__name__)


class EmailWorker(BaseWorker):
    def __init__(self, worker_id: str, browser_manager: BrowserManager, repo: Repository,
                 proxy: dict | None = None, min_pool_size: int = 5):
        super().__init__(worker_id, name=f"EmailReg-{worker_id}")
        self.browser = browser_manager
        self.repo = repo
        self.proxy = proxy
        self.min_pool_size = min_pool_size

    def work(self):
        cfg = load_config()
        rambler_url = cfg["rambler"]["base_url"]

        while not self.should_stop:
            self.wait_if_paused()
            if self.should_stop:
                break

            # Check how many fresh emails we have
            stats = self.repo.get_stats()
            fresh = stats["fresh_emails"]

            if fresh >= self.min_pool_size:
                self._emit_status(f"Email pool OK ({fresh} fresh) — waiting...")
                self._stop_event.wait(timeout=30)
                continue

            self._emit_status(f"Registering new email (pool: {fresh}/{self.min_pool_size})...")

            ctx = self.browser.create_context(proxy=self.proxy)
            try:
                result = register_rambler_email(ctx, rambler_url)
                if result:
                    self.repo.add_email(result["email"], result["password"])
                    self._emit_status(f"Registered: {result['email']}")
                else:
                    self._emit_status("Email registration failed — retrying...")
                    self._stop_event.wait(timeout=15)
            except Exception as e:
                self.log.error(f"Email registration error: {e}")
                self._emit_status(f"Error: {e}")
                self._stop_event.wait(timeout=15)
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
