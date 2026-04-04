"""Coordinator — manages all workers and their lifecycle."""

import logging
from typing import Dict, List

from src.config.settings import AppConfig
from src.db.manager import DatabaseManager
from src.workers.signals import WorkerSignals
from src.workers.parser_worker import ParserWorker
from src.workers.email_worker import EmailWorker

logger = logging.getLogger(__name__)


class WorkerCoordinator:
    """Creates, starts, stops, and monitors all workers."""

    def __init__(self, config: AppConfig, db: DatabaseManager, signals: WorkerSignals):
        self.config = config
        self.db = db
        self.signals = signals
        self._parser_workers: Dict[str, ParserWorker] = {}
        self._email_workers: Dict[str, EmailWorker] = {}
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start_all(self):
        """Initialize pages in DB and start all workers."""
        if self._running:
            return

        # Initialize page queue
        self.db.init_pages(self.config.total_pages)

        # Load proxies from config into DB
        for proxy in self.config.proxies:
            try:
                self.db.add_proxy(proxy)
            except Exception:
                pass  # Already exists

        # Start parser workers
        for i in range(self.config.workers.parser_count):
            worker_id = f"parser_{i+1}"
            worker = ParserWorker(worker_id, self.config, self.db, self.signals)
            self._parser_workers[worker_id] = worker
            worker.start()
            logger.info(f"Started {worker_id}")

        # Start email workers
        for i in range(self.config.workers.email_count):
            worker_id = f"email_{i+1}"
            worker = EmailWorker(worker_id, self.config, self.db, self.signals)
            self._email_workers[worker_id] = worker
            worker.start()
            logger.info(f"Started {worker_id}")

        self._running = True

    def stop_all(self):
        """Signal all workers to stop and wait."""
        if not self._running:
            return

        logger.info("Stopping all workers...")

        for worker in list(self._parser_workers.values()) + list(self._email_workers.values()):
            worker.stop()

        # Wait for workers to finish (with timeout)
        for worker in list(self._parser_workers.values()) + list(self._email_workers.values()):
            worker.join(timeout=10)

        self._parser_workers.clear()
        self._email_workers.clear()
        self._running = False
        logger.info("All workers stopped")

    def pause_all(self):
        for worker in list(self._parser_workers.values()) + list(self._email_workers.values()):
            worker.pause()

    def resume_all(self):
        for worker in list(self._parser_workers.values()) + list(self._email_workers.values()):
            worker.resume()

    def get_worker_ids(self) -> List[str]:
        return list(self._parser_workers.keys()) + list(self._email_workers.keys())

    def get_stats(self) -> dict:
        return self.db.get_stats()
