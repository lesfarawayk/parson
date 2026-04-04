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

        # Release pages that were in-progress during previous run
        stale = self.repo.release_stale_claims()
        if stale:
            log.info(f"Released {stale} stale page claims from previous run")

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
        import time
        stats = self.repo.get_stats()
        # Aggregate worker-level counters (completed pages + current in-progress page)
        agg = {"saved": 0, "filtered": 0, "duplicate": 0, "error": 0}
        all_page_times: list[float] = []
        earliest_start: float | None = None
        for w in self.workers:
            if hasattr(w, "total_stats"):
                for k in agg:
                    agg[k] += w.total_stats.get(k, 0)
            # Add current page stats (in-progress, not yet accumulated)
            cur_ps = getattr(w, "_page_stats", None)
            if cur_ps:
                for k in agg:
                    agg[k] += cur_ps.get(k, 0)
            if hasattr(w, "pages_times"):
                all_page_times.extend(w.pages_times)
            if hasattr(w, "started_at") and w.started_at is not None:
                if earliest_start is None or w.started_at < earliest_start:
                    earliest_start = w.started_at
        stats["worker_saved"] = agg["saved"]
        stats["worker_filtered"] = agg["filtered"]
        stats["worker_duplicate"] = agg["duplicate"]
        stats["worker_error"] = agg["error"]
        stats["elapsed"] = time.monotonic() - earliest_start if earliest_start else 0
        avg_page = sum(all_page_times) / len(all_page_times) if all_page_times else 0
        stats["avg_page_time"] = avg_page

        # ETA: remaining pages / (active workers processing in parallel)
        active_workers = sum(1 for w in self.workers if getattr(w, "page", None) is not None)
        active_workers = max(active_workers, 1)
        pages_remaining = stats["pages_total"] - stats["pages_completed"] - stats["pages_in_progress"]
        pages_remaining = max(pages_remaining, 0)
        stats["eta"] = (pages_remaining / active_workers) * avg_page if avg_page > 0 else 0
        stats["active_workers"] = active_workers
        return stats

    def get_worker_statuses(self) -> list[dict]:
        return [
            {"id": w.worker_id, "state": w.state.value, "message": w.status_message}
            for w in self.workers
        ]

    @property
    def is_running(self) -> bool:
        return self._running
