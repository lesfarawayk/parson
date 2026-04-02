"""
Email worker — buys email accounts from NotLetters API and stores them in the DB.

No browser needed — pure API calls. Maintains a pool of fresh emails for account registration.
"""

import logging
from ..api.notletters import NotLettersClient
from ..db.repository import Repository
from ..config_manager import load_config
from .base_worker import BaseWorker

log = logging.getLogger(__name__)


class EmailWorker(BaseWorker):
    def __init__(self, worker_id: str, repo: Repository, min_pool_size: int = 5):
        super().__init__(worker_id, name=f"EmailBuyer-{worker_id}")
        self.repo = repo
        self.min_pool_size = min_pool_size
        self.client: NotLettersClient | None = None

    def work(self):
        cfg = load_config()
        token = cfg.get("notletters", {}).get("api_token", "")
        if not token:
            self._emit_status("NotLetters API token not configured!")
            return

        self.client = NotLettersClient(token)
        email_type = cfg.get("notletters", {}).get("email_type", 0)
        batch_size = cfg.get("notletters", {}).get("batch_size", 3)

        # Log balance on start
        try:
            info = self.client.get_balance()
            self._emit_status(f"NotLetters balance: {info['balance']}")
        except Exception as e:
            self._emit_status(f"Failed to check balance: {e}")

        while not self.should_stop:
            self.wait_if_paused()
            if self.should_stop:
                break

            stats = self.repo.get_stats()
            fresh = stats["fresh_emails"]

            if fresh >= self.min_pool_size:
                self._emit_status(f"Email pool OK ({fresh} fresh) — waiting...")
                self._stop_event.wait(timeout=30)
                continue

            need = min(batch_size, self.min_pool_size - fresh)
            self._emit_status(f"Buying {need} emails (pool: {fresh}/{self.min_pool_size})...")

            try:
                emails = self.client.buy_emails(count=need, type_email=email_type)
                for em in emails:
                    self.repo.add_email(em.email, em.password)
                    log.info(f"Added email: {em.email}")
                self._emit_status(f"Bought {len(emails)} emails")
            except Exception as e:
                self.log.error(f"Email purchase failed: {e}")
                self._emit_status(f"Purchase error: {e}")
                self._stop_event.wait(timeout=30)
