"""Email worker — registers email accounts and tracker accounts."""

import logging
import threading
import time
from typing import Optional

from src.config.settings import AppConfig
from src.db.manager import DatabaseManager
from src.browser.email_automation import RamblerRegistration, TrackerRegistration
from src.workers.signals import WorkerSignals

logger = logging.getLogger(__name__)


class EmailWorker(threading.Thread):
    """Worker that continuously registers new email + tracker accounts.

    Flow:
    1. Register new email on Rambler
    2. Save email to DB
    3. Register new tracker account using that email
    4. Save tracker account to DB
    5. Repeat
    """

    def __init__(self, worker_id: str, config: AppConfig, db: DatabaseManager,
                 signals: WorkerSignals, min_accounts: int = 5):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self.config = config
        self.db = db
        self.signals = signals
        self.min_accounts = min_accounts  # Keep at least this many fresh accounts
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()

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

    def run(self):
        self._emit_log("Email worker starting...")
        self._emit_status("starting")

        email_reg = None
        tracker_reg = None

        try:
            proxy = self.db.get_proxy()

            while not self._stop_event.is_set():
                self._pause_event.wait()
                if self._stop_event.is_set():
                    break

                # Check if we need more accounts
                stats = self.db.get_stats()
                active_accounts = stats.get('active_accounts', 0)

                if active_accounts >= self.min_accounts:
                    self._emit_log(f"Enough accounts ({active_accounts}), sleeping 30s...")
                    self._emit_status("idle")
                    self._stop_event.wait(30)
                    continue

                self._emit_status("registering_email")
                self._emit_log("Registering new email account...")

                # Step 1: Register email
                email_reg = RamblerRegistration(self.worker_id, self.config, proxy)
                email_reg.start()

                result = email_reg.register_email()
                email_reg.stop()
                email_reg = None

                if not result:
                    self._emit_log("Email registration failed, retrying in 30s...")
                    self.signals.error.emit(self.worker_id, "Email registration failed")
                    self._stop_event.wait(30)
                    continue

                email_addr, email_pwd = result
                self.db.add_email_account(email_addr, email_pwd, self.config.email.provider)
                self._emit_log(f"Email registered: {email_addr}")

                # Step 2: Register tracker account
                self._emit_status("registering_tracker")
                self._emit_log("Registering tracker account...")

                tracker_reg = TrackerRegistration(self.worker_id, self.config, proxy)
                tracker_reg.start()

                reg_result = tracker_reg.register_account(email_addr, email_pwd)
                tracker_reg.stop()
                tracker_reg = None

                if not reg_result:
                    self._emit_log("Tracker registration failed, retrying in 30s...")
                    self.signals.error.emit(self.worker_id, "Tracker registration failed")
                    self._stop_event.wait(30)
                    continue

                username, password = reg_result
                self.db.create_account(username, password, email_addr)
                self._emit_log(f"Tracker account created: {username}")

                # Small delay between registrations
                self._emit_status("cooldown")
                self._stop_event.wait(10)

        except Exception as e:
            self._emit_log(f"Fatal error: {e}")
            self.signals.error.emit(self.worker_id, str(e))
        finally:
            if email_reg:
                email_reg.stop()
            if tracker_reg:
                tracker_reg.stop()
            self._emit_status("stopped")
            self.signals.finished.emit(self.worker_id)
