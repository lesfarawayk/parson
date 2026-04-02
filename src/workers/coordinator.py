"""
Worker coordinator — manages the lifecycle of all workers.

Responsibilities:
- Start/stop/pause parser workers, email workers, account workers
- Distribute proxies across workers
- Relay status updates to the GUI
- Handle graceful shutdown
"""

import logging
import threading
from typing import Callable

from ..browser.browser_manager import BrowserManager
from ..db.repository import Repository
from ..config_manager import load_config
from .parser_worker import ParserWorker
from .email_worker import EmailWorker
from .account_worker import AccountWorker
from .base_worker import BaseWorker

log = logging.getLogger(__name__)


class WorkerCoordinator:
    def __init__(self):
        self.repo = Repository()
        self.browser_manager = BrowserManager()
        self.workers: list[BaseWorker] = []
        self._status_callback: Callable | None = None
        self._running = False

    def set_status_callback(self, cb: Callable):
        """Set a callback: cb(worker_id, state, message)."""
        self._status_callback = cb

    def _get_proxy(self, index: int) -> dict | None:
        """Get proxy for worker by index from config."""
        cfg = load_config()
        if not cfg["proxy"]["enabled"]:
            return None
        proxies = cfg["proxy"]["list"]
        if not proxies:
            return None
        proxy_str = proxies[index % len(proxies)]
        # Parse proxy string: protocol://user:pass@host:port or protocol://host:port
        if "@" in proxy_str:
            proto_userpass, hostport = proxy_str.rsplit("@", 1)
            proto, userpass = proto_userpass.split("://", 1)
            user, pwd = userpass.split(":", 1)
            return {"server": f"{proto}://{hostport}", "username": user, "password": pwd}
        else:
            return {"server": proxy_str}

    def start(self):
        """Launch browser and all workers."""
        if self._running:
            return
        self._running = True
        cfg = load_config()

        log.info("Starting browser...")
        self.browser_manager.start()

        # Start email buyer worker(s) — pure API, no browser needed
        for i in range(cfg["workers"].get("email_reg_count", 1)):
            wid = f"email-{i}"
            w = EmailWorker(wid, self.repo)
            if self._status_callback:
                w.add_status_callback(self._status_callback)
            self.workers.append(w)
            w.start()

        # Start account registration worker (1 by default)
        wid = "account-0"
        w = AccountWorker(wid, self.browser_manager, self.repo, proxy=self._get_proxy(200))
        if self._status_callback:
            w.add_status_callback(self._status_callback)
        self.workers.append(w)
        w.start()

        # Start parser workers
        for i in range(cfg["workers"].get("parser_count", 3)):
            wid = f"parser-{i}"
            w = ParserWorker(wid, self.browser_manager, self.repo, proxy=self._get_proxy(i))
            if self._status_callback:
                w.add_status_callback(self._status_callback)
            self.workers.append(w)
            w.start()

        log.info(f"Started {len(self.workers)} workers")

    def stop(self):
        """Gracefully stop all workers and the browser."""
        if not self._running:
            return
        log.info("Stopping all workers...")
        for w in self.workers:
            w.request_stop()
        for w in self.workers:
            w.join(timeout=15)
        self.workers.clear()
        self.browser_manager.stop()
        self._running = False
        log.info("All workers stopped")

    def pause_all(self):
        for w in self.workers:
            w.request_pause()

    def resume_all(self):
        for w in self.workers:
            w.request_resume()

    def get_stats(self) -> dict:
        return self.repo.get_stats()

    def get_worker_statuses(self) -> list[dict]:
        return [
            {
                "id": w.worker_id,
                "state": w.state.value,
                "message": w.status_message,
            }
            for w in self.workers
        ]

    @property
    def is_running(self) -> bool:
        return self._running
