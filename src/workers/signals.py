"""Qt signals for worker-GUI communication."""

from PySide6.QtCore import QObject, Signal


class WorkerSignals(QObject):
    """Signals emitted by workers to update the GUI."""
    log = Signal(str, str)           # (worker_id, message)
    status = Signal(str, str)        # (worker_id, status)
    progress = Signal(str, int, int) # (worker_id, current, total)
    stats_updated = Signal(dict)     # global stats dict
    error = Signal(str, str)         # (worker_id, error_message)
    finished = Signal(str)           # worker_id
