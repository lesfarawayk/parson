"""
Worker coordinator — manages the lifecycle of parser workers.

Each worker creates its own Playwright + browser (thread requirement).
No shared browser manager needed.
"""

import logging
from typing import Callable

from ..db.repository import Repository
from ..config_manager import load_config
from .parser_worker import ParserWorker
from .base_worker import BaseWorker

log = logging.getLogger(__name__)


class WorkerCoordinator:
    def __init__(self):
        self.repo = Repository()
        self.workers: list[BaseWorker] = []
        self._status_callback: Callable | None = None
        self._running = False

    def set_status_callback(self, cb: Callable):
        self._status_callback = cb

    def _get_proxy(self, index: int) -> dict | None:
        cfg = load_config()
        if not cfg["proxy"]["enabled"]:
            return None
        proxies = cfg["proxy"]["list"]
        if not proxies:
            return None
        proxy_str = proxies[index % len(proxies)]
        if "@" in proxy_str:
            proto_userpass, hostport = proxy_str.rsplit("@", 1)
            proto, userpass = proto_userpass.split("://", 1)
            user, pwd = userpass.split(":", 1)
            return {"server": f"{proto}://{hostport}", "username": user, "password": pwd}
        else:
            return {"server": proxy_str}

    def start(self):
        """Launch parser workers (each starts its own browser)."""
        if self._running:
            return
        self._running = True
        cfg = load_config()

        stats = self.repo.get_stats()
        if stats["fresh_emails"] == 0:
            log.warning("No fresh emails in DB — add emails before starting!")

        for i in range(cfg["workers"].get("parser_count", 3)):
            wid = f"parser-{i}"
            w = ParserWorker(wid, self.repo, proxy=self._get_proxy(i))
            if self._status_callback:
                w.add_status_callback(self._status_callback)
            self.workers.append(w)
            w.start()

        log.info(f"Started {len(self.workers)} parser workers (each with own browser)")

    def stop(self):
        """Gracefully stop all workers (each cleans up its own browser)."""
        if not self._running:
            return
        log.info("Stopping all workers...")
        for w in self.workers:
            w.request_stop()
        for w in self.workers:
            w.join(timeout=30)
        self.workers.clear()
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
            {"id": w.worker_id, "state": w.state.value, "message": w.status_message}
            for w in self.workers
        ]

    @property
    def is_running(self) -> bool:
        return self._running
