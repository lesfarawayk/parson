"""Base worker class with common lifecycle, logging, and signal support."""

import logging
import threading
from enum import Enum


class WorkerState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class BaseWorker(threading.Thread):
    def __init__(self, worker_id: str, name: str = None):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self._name = name or worker_id
        self.state = WorkerState.IDLE
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially
        self.log = logging.getLogger(f"worker.{worker_id}")
        self.status_message = "Idle"
        self._callbacks = []  # list of callable(worker_id, state, message)

    def add_status_callback(self, cb):
        self._callbacks.append(cb)

    def _emit_status(self, message: str):
        self.status_message = message
        for cb in self._callbacks:
            try:
                cb(self.worker_id, self.state.value, message)
            except Exception:
                pass

    def request_stop(self):
        self.state = WorkerState.STOPPING
        self._stop_event.set()
        self._pause_event.set()  # unblock if paused
        self._emit_status("Stopping...")

    def request_pause(self):
        self.state = WorkerState.PAUSED
        self._pause_event.clear()
        self._emit_status("Paused")

    def request_resume(self):
        self.state = WorkerState.RUNNING
        self._pause_event.set()
        self._emit_status("Resumed")

    @property
    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def wait_if_paused(self):
        """Block until resumed or stopped."""
        self._pause_event.wait()

    def run(self):
        self.state = WorkerState.RUNNING
        self._emit_status("Started")
        try:
            self.work()
        except Exception as e:
            self.state = WorkerState.ERROR
            self._emit_status(f"Error: {e}")
            self.log.exception(f"Worker {self.worker_id} crashed")
        finally:
            if self.state != WorkerState.ERROR:
                self.state = WorkerState.STOPPED
                self._emit_status("Stopped")

    def work(self):
        """Override in subclasses."""
        raise NotImplementedError
