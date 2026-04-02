"""Main application window — PySide6 GUI for the torrent parser."""

import json
import logging
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPushButton, QLabel, QTextEdit, QTableWidget, QTableWidgetItem,
    QGroupBox, QFormLayout, QLineEdit, QSpinBox, QListWidget, QComboBox,
    QHeaderView, QSplitter, QMessageBox, QPlainTextEdit, QScrollArea,
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor

from ..workers.coordinator import WorkerCoordinator
from ..config_manager import load_config, save_config
from ..browser.tracker_profiles import list_profiles, PROFILES

log = logging.getLogger(__name__)


class StatusSignal(QObject):
    """Bridge between worker threads and Qt GUI thread."""
    updated = Signal(str, str, str)  # worker_id, state, message


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Parson — Torrent Parser")
        self.setMinimumSize(1000, 700)

        self.coordinator = WorkerCoordinator()
        self.status_signal = StatusSignal()
        self.status_signal.updated.connect(self._on_worker_status)
        self.coordinator.set_status_callback(
            lambda wid, state, msg: self.status_signal.updated.emit(wid, state, msg)
        )

        self._build_ui()
        self._load_config_to_ui()

        # Periodic stats refresh
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self._refresh_stats)
        self.stats_timer.start(3000)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Top control bar
        ctrl_bar = QHBoxLayout()
        self.btn_start = QPushButton("Start")
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_stop.setEnabled(False)
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.clicked.connect(self._on_pause)
        self.btn_pause.setEnabled(False)
        self.btn_save_cfg = QPushButton("Save Config")
        self.btn_save_cfg.clicked.connect(self._on_save_config)

        ctrl_bar.addWidget(self.btn_start)
        ctrl_bar.addWidget(self.btn_stop)
        ctrl_bar.addWidget(self.btn_pause)
        ctrl_bar.addStretch()
        ctrl_bar.addWidget(self.btn_save_cfg)
        layout.addLayout(ctrl_bar)

        # Tabs
        tabs = QTabWidget()
        tabs.addTab(self._build_dashboard_tab(), "Dashboard")
        tabs.addTab(self._build_config_tab(), "Settings")
        tabs.addTab(self._build_log_tab(), "Log")
        layout.addWidget(tabs)

    # ── Dashboard tab ───────────────────────────────────────────

    def _build_dashboard_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        splitter = QSplitter(Qt.Vertical)

        # Stats group
        stats_group = QGroupBox("Statistics")
        stats_layout = QHBoxLayout(stats_group)
        self.lbl_stats = QLabel("No data yet")
        self.lbl_stats.setWordWrap(True)
        stats_layout.addWidget(self.lbl_stats)
        splitter.addWidget(stats_group)

        # Workers table
        workers_group = QGroupBox("Workers")
        wl = QVBoxLayout(workers_group)
        self.workers_table = QTableWidget(0, 3)
        self.workers_table.setHorizontalHeaderLabels(["Worker", "State", "Status"])
        self.workers_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.workers_table.setEditTriggers(QTableWidget.NoEditTriggers)
        wl.addWidget(self.workers_table)
        splitter.addWidget(workers_group)

        layout.addWidget(splitter)
        return w

    # ── Config tab ──────────────────────────────────────────────

    def _build_config_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # Tracker settings
        tracker_group = QGroupBox("Tracker")
        tl = QFormLayout(tracker_group)
        self.cfg_tracker_profile = QComboBox()
        for name in list_profiles():
            profile = PROFILES[name]
            self.cfg_tracker_profile.addItem(f"{profile.name} ({profile.base_url})", name)
        self.cfg_tracker_url_label = QLabel()
        self.cfg_tracker_profile.currentIndexChanged.connect(self._on_tracker_profile_changed)
        self.cfg_category_id = QLineEdit()
        self.cfg_pages_start = QSpinBox(); self.cfg_pages_start.setRange(1, 9999)
        self.cfg_pages_end = QSpinBox(); self.cfg_pages_end.setRange(1, 9999)
        self.cfg_max_dl = QSpinBox(); self.cfg_max_dl.setRange(1, 50)
        tl.addRow("Tracker:", self.cfg_tracker_profile)
        tl.addRow("", self.cfg_tracker_url_label)
        tl.addRow("Category ID:", self.cfg_category_id)
        tl.addRow("Pages start:", self.cfg_pages_start)
        tl.addRow("Pages end:", self.cfg_pages_end)
        tl.addRow("Max downloads/account:", self.cfg_max_dl)
        layout.addWidget(tracker_group)

        # Workers settings
        workers_group = QGroupBox("Workers")
        wl = QFormLayout(workers_group)
        self.cfg_parser_count = QSpinBox(); self.cfg_parser_count.setRange(1, 20)
        self.cfg_email_count = QSpinBox(); self.cfg_email_count.setRange(0, 5)
        self.cfg_download_dir = QLineEdit()
        wl.addRow("Parser workers:", self.cfg_parser_count)
        wl.addRow("Email reg workers:", self.cfg_email_count)
        wl.addRow("Download dir:", self.cfg_download_dir)
        layout.addWidget(workers_group)

        # Tags
        tags_group = QGroupBox("Tags")
        tags_layout = QFormLayout(tags_group)
        self.cfg_dl_tags = QLineEdit()
        self.cfg_dl_tags.setPlaceholderText("Comma-separated: 4K, 2160p, UHD")
        self.cfg_rec_tags = QLineEdit()
        self.cfg_rec_tags.setPlaceholderText("Comma-separated: военное, зарубежное, драма")
        tags_layout.addRow("Download tags:", self.cfg_dl_tags)
        tags_layout.addRow("Record tags:", self.cfg_rec_tags)
        layout.addWidget(tags_group)

        # NotLetters
        nl_group = QGroupBox("NotLetters (Email Provider)")
        nl_layout = QFormLayout(nl_group)
        self.cfg_nl_token = QLineEdit()
        self.cfg_nl_token.setPlaceholderText("API token from notletters.com")
        self.cfg_nl_token.setEchoMode(QLineEdit.Password)
        self.cfg_nl_email_type = QComboBox()
        self.cfg_nl_email_type.addItems(["Limited (0)", "Unlimited (1)", "RU zone (2)", "Personal (3)"])
        self.cfg_nl_batch = QSpinBox(); self.cfg_nl_batch.setRange(1, 20)
        nl_layout.addRow("API Token:", self.cfg_nl_token)
        nl_layout.addRow("Email type:", self.cfg_nl_email_type)
        nl_layout.addRow("Batch size:", self.cfg_nl_batch)
        layout.addWidget(nl_group)

        # Proxy
        proxy_group = QGroupBox("Proxy")
        pl = QVBoxLayout(proxy_group)
        self.cfg_proxy_list = QTextEdit()
        self.cfg_proxy_list.setPlaceholderText("One proxy per line: protocol://user:pass@host:port")
        self.cfg_proxy_list.setMaximumHeight(120)
        pl.addWidget(QLabel("Proxy list (one per line):"))
        pl.addWidget(self.cfg_proxy_list)
        layout.addWidget(proxy_group)

        layout.addStretch()
        return w

    # ── Log tab ─────────────────────────────────────────────────

    def _build_log_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumBlockCount(5000)
        layout.addWidget(self.log_area)

        # Attach a log handler
        handler = QtLogHandler(self.log_area)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

        return w

    def _on_tracker_profile_changed(self, index):
        name = self.cfg_tracker_profile.currentData()
        if name and name in PROFILES:
            p = PROFILES[name]
            self.cfg_tracker_url_label.setText(f"URL: {p.base_url}")

    # ── Config I/O ──────────────────────────────────────────────

    def _load_config_to_ui(self):
        cfg = load_config()
        # Set tracker profile dropdown
        profile_name = cfg["tracker"].get("profile", "rutracker")
        idx = self.cfg_tracker_profile.findData(profile_name)
        if idx >= 0:
            self.cfg_tracker_profile.setCurrentIndex(idx)
        self._on_tracker_profile_changed(0)

        self.cfg_category_id.setText(cfg["tracker"]["category_id"])
        self.cfg_pages_start.setValue(cfg["tracker"]["pages_start"])
        self.cfg_pages_end.setValue(cfg["tracker"]["pages_end"])
        self.cfg_max_dl.setValue(cfg["tracker"]["max_downloads_per_account"])

        self.cfg_parser_count.setValue(cfg["workers"]["parser_count"])
        self.cfg_email_count.setValue(cfg["workers"]["email_reg_count"])
        self.cfg_download_dir.setText(cfg["workers"]["download_dir"])

        self.cfg_dl_tags.setText(", ".join(cfg["tags"]["download_tags"]))
        self.cfg_rec_tags.setText(", ".join(cfg["tags"]["record_tags"]))

        proxies = cfg["proxy"].get("list", [])
        self.cfg_proxy_list.setPlainText("\n".join(proxies))

        nl = cfg.get("notletters", {})
        self.cfg_nl_token.setText(nl.get("api_token", ""))
        self.cfg_nl_email_type.setCurrentIndex(nl.get("email_type", 0))
        self.cfg_nl_batch.setValue(nl.get("batch_size", 3))

    def _ui_to_config(self) -> dict:
        proxy_lines = [l.strip() for l in self.cfg_proxy_list.toPlainText().strip().split("\n") if l.strip()]
        return {
            "tracker": {
                "profile": self.cfg_tracker_profile.currentData() or "rutracker",
                "category_id": self.cfg_category_id.text().strip(),
                "pages_start": self.cfg_pages_start.value(),
                "pages_end": self.cfg_pages_end.value(),
                "max_downloads_per_account": self.cfg_max_dl.value(),
            },
            "workers": {
                "parser_count": self.cfg_parser_count.value(),
                "email_reg_count": self.cfg_email_count.value(),
                "download_dir": self.cfg_download_dir.text().strip() or "downloads",
            },
            "tags": {
                "download_tags": [t.strip() for t in self.cfg_dl_tags.text().split(",") if t.strip()],
                "record_tags": [t.strip() for t in self.cfg_rec_tags.text().split(",") if t.strip()],
            },
            "proxy": {
                "enabled": len(proxy_lines) > 0,
                "list": proxy_lines,
            },
            "notletters": {
                "api_token": self.cfg_nl_token.text().strip(),
                "email_type": self.cfg_nl_email_type.currentIndex(),
                "batch_size": self.cfg_nl_batch.value(),
            },
        }

    def _on_save_config(self):
        cfg = self._ui_to_config()
        save_config(cfg)
        QMessageBox.information(self, "Config", "Configuration saved.")

    # ── Control buttons ─────────────────────────────────────────

    def _on_start(self):
        # Save config first
        cfg = self._ui_to_config()
        save_config(cfg)

        if not cfg["tracker"]["category_id"]:
            QMessageBox.warning(self, "Error", "Please set a category ID first.")
            return

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("Pause")

        try:
            self.coordinator.start()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start: {e}")
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)

    def _on_stop(self):
        self.btn_stop.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.coordinator.stop()
        self.btn_start.setEnabled(True)

    def _on_pause(self):
        if self.btn_pause.text() == "Pause":
            self.coordinator.pause_all()
            self.btn_pause.setText("Resume")
        else:
            self.coordinator.resume_all()
            self.btn_pause.setText("Pause")

    # ── Status updates ──────────────────────────────────────────

    def _on_worker_status(self, worker_id: str, state: str, message: str):
        """Update workers table from signal (thread-safe)."""
        for row in range(self.workers_table.rowCount()):
            if self.workers_table.item(row, 0) and self.workers_table.item(row, 0).text() == worker_id:
                self.workers_table.setItem(row, 1, QTableWidgetItem(state))
                self.workers_table.setItem(row, 2, QTableWidgetItem(message))
                # Color by state
                color = {"running": QColor(200, 255, 200), "error": QColor(255, 200, 200),
                         "paused": QColor(255, 255, 200), "stopped": QColor(220, 220, 220)}.get(state)
                if color:
                    for col in range(3):
                        item = self.workers_table.item(row, col)
                        if item:
                            item.setBackground(color)
                return

        # New worker — add row
        row = self.workers_table.rowCount()
        self.workers_table.insertRow(row)
        self.workers_table.setItem(row, 0, QTableWidgetItem(worker_id))
        self.workers_table.setItem(row, 1, QTableWidgetItem(state))
        self.workers_table.setItem(row, 2, QTableWidgetItem(message))

    def _refresh_stats(self):
        if not self.coordinator.is_running:
            return
        try:
            stats = self.coordinator.get_stats()
            text = (
                f"Torrents: {stats['total_torrents']}  |  "
                f"Downloaded: {stats['downloaded']}  |  "
                f"Tagged: {stats['tagged']}  |  "
                f"Skipped: {stats['skipped']}  |  "
                f"Errors: {stats['errors']}\n"
                f"Fresh emails: {stats['fresh_emails']}  |  "
                f"Fresh accounts: {stats['fresh_accounts']}  |  "
                f"Pages done: {stats['pages_completed']}"
            )
            self.lbl_stats.setText(text)
        except Exception:
            pass

    def closeEvent(self, event):
        if self.coordinator.is_running:
            reply = QMessageBox.question(
                self, "Quit", "Workers are running. Stop them and quit?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            self.coordinator.stop()
        event.accept()


class QtLogHandler(logging.Handler):
    """Logging handler that writes to a QPlainTextEdit."""
    def __init__(self, text_widget: QPlainTextEdit):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record)
        try:
            self.text_widget.appendPlainText(msg)
        except RuntimeError:
            pass  # widget might be deleted
