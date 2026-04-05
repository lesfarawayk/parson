"""
Worker coordinator — manages the lifecycle of parser workers.

Each worker creates its own Playwright + browser (thread requirement).
Watchdog thread monitors workers and restarts crashed ones.
"""

import logging
import os
import threading
import time
from typing import Callable

from ..db.repository import Repository
from ..config_manager import load_config
from .parser_worker import ParserWorker
from .download_worker import DownloadWorker
from .base_worker import BaseWorker, WorkerState

log = logging.getLogger(__name__)


def _memory_mb() -> float:
    """Current process RSS in MB (works on Windows and Linux)."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        pass
    # Fallback for Windows without psutil
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(pmc)
        handle = kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize / (1024 * 1024)
    except Exception:
        pass
    return 0.0


class WorkerCoordinator:
    def __init__(self):
        self.repo = Repository()
        self.workers: list[BaseWorker] = []
        self._status_callback: Callable | None = None
        self._running = False
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop = threading.Event()
        self._max_restarts = 5  # per worker
        self._restart_counts: dict[str, int] = {}

        # Download workers (separate pool)
        self.dl_workers: list[BaseWorker] = []
        self._dl_status_callback: Callable | None = None
        self._dl_running = False
        self.dl_repo: Repository | None = None  # optional separate DB for downloads

    def set_status_callback(self, cb: Callable):
        self._status_callback = cb

    def set_dl_status_callback(self, cb: Callable):
        self._dl_status_callback = cb

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

        log.info(f"Memory at start: {_memory_mb():.0f} MB")

        for i in range(cfg["workers"].get("parser_count", 3)):
            wid = f"parser-{i}"
            self._restart_counts[wid] = 0
            w = ParserWorker(wid, self.repo, proxy=self._get_proxy(i))
            if self._status_callback:
                w.add_status_callback(self._status_callback)
            self.workers.append(w)
            w.start()

        log.info(f"Started {len(self.workers)} parser workers (each with own browser)")

        # Start watchdog
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()

    def _watchdog_loop(self):
        """Monitor workers, restart crashed ones, log memory."""
        log.info("Watchdog started")
        last_memory_log = 0
        while not self._watchdog_stop.wait(timeout=10):
            # Log memory every 2 minutes
            now = time.monotonic()
            if now - last_memory_log > 120:
                log.info(f"Memory: {_memory_mb():.0f} MB, workers: {len(self.workers)}")
                last_memory_log = now

            # Check for dead workers
            for i, w in enumerate(list(self.workers)):
                if not w.is_alive() and w.state in (WorkerState.ERROR, WorkerState.STOPPED):
                    wid = w.worker_id
                    restarts = self._restart_counts.get(wid, 0)

                    if w.state == WorkerState.ERROR and restarts < self._max_restarts:
                        self._restart_counts[wid] = restarts + 1
                        log.warning(
                            f"Watchdog: {wid} died (state={w.state.value}), "
                            f"restarting ({restarts + 1}/{self._max_restarts})... "
                            f"Memory: {_memory_mb():.0f} MB"
                        )
                        new_w = ParserWorker(wid, self.repo, proxy=self._get_proxy(i))
                        if self._status_callback:
                            new_w.add_status_callback(self._status_callback)
                        self.workers[i] = new_w
                        new_w.start()
                    elif w.state == WorkerState.ERROR and restarts >= self._max_restarts:
                        log.error(f"Watchdog: {wid} exceeded max restarts ({self._max_restarts}), giving up")

            # Check if ALL workers are done (stopped, not error)
            all_done = all(
                not w.is_alive() and w.state == WorkerState.STOPPED
                for w in self.workers
            )
            if all_done:
                log.info("Watchdog: all workers finished successfully")
                break

        log.info("Watchdog stopped")

    def stop(self):
        """Gracefully stop all workers (each cleans up its own browser)."""
        if not self._running:
            return
        log.info("Stopping all workers...")
        self._watchdog_stop.set()
        for w in self.workers:
            w.request_stop()
        for w in self.workers:
            w.join(timeout=30)
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=5)
        self.workers.clear()
        self._restart_counts.clear()
        self._running = False
        log.info(f"All workers stopped. Memory: {_memory_mb():.0f} MB")

    def pause_all(self):
        for w in self.workers:
            w.request_pause()

    def resume_all(self):
        for w in self.workers:
            w.request_resume()

    def get_stats(self) -> dict:
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
        stats["memory_mb"] = _memory_mb()

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

    # ── Download workers ──────────────────────────────────────

    def start_downloads(self, worker_count: int = 1):
        """Launch download workers to fetch .torrent files."""
        if self._dl_running:
            return
        self._dl_running = True

        repo = self.dl_repo or self.repo

        # Release stuck DOWNLOADING entries from previous run
        released = repo.release_downloading()
        if released:
            log.info(f"Released {released} stuck DOWNLOADING torrents")

        for i in range(worker_count):
            wid = f"dl-{i}"
            w = DownloadWorker(wid, repo, proxy=self._get_proxy(i))
            if self._dl_status_callback:
                w.add_status_callback(self._dl_status_callback)
            self.dl_workers.append(w)
            w.start()

        log.info(f"Started {worker_count} download workers")

    def stop_downloads(self):
        """Stop all download workers."""
        if not self._dl_running:
            return
        log.info("Stopping download workers...")
        for w in self.dl_workers:
            w.request_stop()
        for w in self.dl_workers:
            w.join(timeout=30)
        self.dl_workers.clear()
        self._dl_running = False
        log.info("Download workers stopped")

    def get_download_stats(self) -> dict:
        """Stats for the download tab."""
        repo = self.dl_repo or self.repo
        stats = repo.get_download_stats()
        # Aggregate from workers
        agg = {"downloaded": 0, "errors": 0}
        for w in self.dl_workers:
            if hasattr(w, "total_stats"):
                agg["downloaded"] += w.total_stats.get("downloaded", 0)
                agg["errors"] += w.total_stats.get("errors", 0)
        stats["session_downloaded"] = agg["downloaded"]
        stats["session_errors"] = agg["errors"]
        return stats

    @property
    def is_dl_running(self) -> bool:
        return self._dl_running
