"""Main application window — PySide6 GUI for the torrent parser."""

import json
import logging
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPushButton, QLabel, QTextEdit, QTableWidget, QTableWidgetItem,
    QGroupBox, QFormLayout, QLineEdit, QSpinBox, QListWidget, QComboBox,
    QHeaderView, QSplitter, QMessageBox, QPlainTextEdit, QScrollArea, QCheckBox,
    QProgressBar,
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QRect
from PySide6.QtGui import QColor, QPixmap, QPainter, QBrush, QPen, QFont as QGuiFont

from ..workers.coordinator import WorkerCoordinator
from ..config_manager import load_config, save_config
from ..browser.tracker_profiles import list_profiles, PROFILES

log = logging.getLogger(__name__)


def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec}s"
    h, mins = divmod(m, 60)
    return f"{h}h {mins}m"


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
        tabs.addTab(self._build_database_tab(), "Database")
        tabs.addTab(self._build_emails_tab(), "Emails")
        tabs.addTab(self._build_blocked_domains_tab(), "Blocked Domains")
        tabs.addTab(self._build_pages_tab(), "Pages")
        tabs.addTab(self._build_downloader_tab(), "Downloader")
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
        stats_layout = QVBoxLayout(stats_group)
        self.lbl_stats = QLabel("No data yet")
        self.lbl_stats.setWordWrap(True)
        stats_layout.addWidget(self.lbl_stats)

        # Page progress bar
        progress_row = QHBoxLayout()
        self.lbl_pages = QLabel("Pages: —")
        self.page_progress = QProgressBar()
        self.page_progress.setMinimum(0)
        self.page_progress.setMaximum(100)
        self.page_progress.setValue(0)
        self.page_progress.setTextVisible(True)
        self.page_progress.setFormat("%v / %m  (%p%)")
        progress_row.addWidget(self.lbl_pages)
        progress_row.addWidget(self.page_progress, 1)
        stats_layout.addLayout(progress_row)

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

    # ── Database tab ───────────────────────────────────────────

    def _build_database_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # Top controls
        ctrl = QHBoxLayout()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_db_table)

        self.db_filter = QComboBox()
        self.db_filter.addItem("All", "")
        self.db_filter.addItem("Parsed", "parsed")
        self.db_filter.addItem("Approved", "approved")
        self.db_filter.addItem("Rejected", "rejected")
        self.db_filter.addItem("Skipped", "skipped")
        self.db_filter.addItem("Error", "error")
        self.db_filter.currentIndexChanged.connect(self._refresh_db_table)

        self.db_count_label = QLabel("")

        ctrl.addWidget(QLabel("Filter:"))
        ctrl.addWidget(self.db_filter)
        ctrl.addWidget(btn_refresh)
        btn_clear_db = QPushButton("Clear Database")
        btn_clear_db.clicked.connect(self._on_clear_database)

        ctrl.addWidget(self.db_count_label)
        ctrl.addStretch()
        ctrl.addWidget(btn_clear_db)
        layout.addLayout(ctrl)

        # Main table
        columns = [
            "ID", "Studio", "Actors", "Film Name", "Year", "Formats",
            "Tags", "Duration", "Size", "Seeds", "Peers", "Status",
        ]
        self.db_table = QTableWidget(0, len(columns))
        self.db_table.setHorizontalHeaderLabels(columns)
        self.db_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)  # Film Name
        self.db_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)  # Tags
        self.db_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.db_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.db_table.setAlternatingRowColors(True)
        self.db_table.setSortingEnabled(True)
        layout.addWidget(self.db_table)

        # Detail panel at the bottom
        detail_group = QGroupBox("Details (select a row)")
        dl = QHBoxLayout(detail_group)

        # Cover image preview
        self.db_cover_label = QLabel()
        self.db_cover_label.setFixedSize(160, 220)
        self.db_cover_label.setStyleSheet("border: 1px solid #ccc; background: #f0f0f0;")
        self.db_cover_label.setAlignment(Qt.AlignCenter)
        self.db_cover_label.setText("No cover")
        dl.addWidget(self.db_cover_label)

        # Text details
        self.db_detail = QPlainTextEdit()
        self.db_detail.setReadOnly(True)
        self.db_detail.setMaximumHeight(220)
        dl.addWidget(self.db_detail, 1)
        layout.addWidget(detail_group)

        self.db_table.currentCellChanged.connect(self._on_db_row_selected)

        self._refresh_db_table()
        return w

    def _refresh_db_table(self):
        status_filter = self.db_filter.currentData() if self.db_filter.currentData() else ""
        rows = self.coordinator.repo.get_all_torrents(status_filter=status_filter)
        self.db_count_label.setText(f"  ({len(rows)} records)")

        self.db_table.setSortingEnabled(False)
        self.db_table.setRowCount(len(rows))

        status_colors = {
            "parsed": QColor(200, 230, 255),
            "approved": QColor(200, 255, 200),
            "rejected": QColor(255, 220, 220),
            "skipped": QColor(240, 240, 240),
            "error": QColor(255, 180, 180),
            "downloading": QColor(255, 255, 200),
            "downloaded": QColor(180, 255, 180),
        }

        self._db_rows_by_id = {str(r["topic_id"]): r for r in rows}

        def _jp(raw):
            """JSON list → comma string."""
            if not raw or raw == "[]":
                return ""
            try:
                items = json.loads(raw)
                return ", ".join(str(i) for i in items) if isinstance(items, list) else raw
            except Exception:
                return str(raw)

        for row_idx, r in enumerate(rows):
            cells = [
                str(r["topic_id"]),
                r["studio"],
                _jp(r.get("actors", "[]")),
                r["film_name"],
                r["year"],
                _jp(r["formats"]),
                _jp(r["tags"]),
                r["duration"],
                r["file_size"],
                str(r["seeds"]),
                str(r["peers"]),
                r["status"],
            ]

            color = status_colors.get(r["status"])
            for col_idx, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if color:
                    item.setBackground(color)
                self.db_table.setItem(row_idx, col_idx, item)

        self.db_table.setSortingEnabled(True)

    def _on_db_row_selected(self, row, col, prev_row, prev_col):
        if row < 0:
            return
        # Get topic_id from table cell (column 0) to handle sorting correctly
        topic_item = self.db_table.item(row, 0)
        if not topic_item or not hasattr(self, "_db_rows_by_id"):
            return
        r = self._db_rows_by_id.get(topic_item.text())
        if not r:
            return
        try:
            devices = ", ".join(json.loads(r["devices"])) if r["devices"] != "[]" else ""
        except Exception:
            devices = r["devices"]
        try:
            actors = ", ".join(json.loads(r.get("actors", "[]"))) if r.get("actors", "[]") != "[]" else ""
        except Exception:
            actors = r.get("actors", "")
        lines = [
            f"Topic ID: {r['topic_id']}    Studio: {r['studio']}    Year: {r['year']}",
            f"Actors: {actors}",
            f"Film: {r['film_name']}",
            f"Devices: {devices}    Size: {r['file_size']}    Duration: {r['duration']}",
            f"Seeds: {r['seeds']}  Peers: {r['peers']}",
            f"Download URL: {r['download_url']}",
        ]
        self.db_detail.setPlainText("\n".join(lines))

        # Load cover from DB
        self.db_cover_label.clear()
        self.db_cover_label.setText("No cover")
        try:
            cover_bytes = self.coordinator.repo.get_cover_data(r["topic_id"])
            if cover_bytes:
                pixmap = QPixmap()
                pixmap.loadFromData(cover_bytes)
                if not pixmap.isNull():
                    self.db_cover_label.setPixmap(
                        pixmap.scaled(160, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )
                    self.db_cover_label.setText("")
        except Exception:
            pass

    def _on_clear_database(self):
        reply = QMessageBox.question(
            self, "Clear Database",
            "Delete ALL parsed torrents and reset page progress?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            count = self.coordinator.repo.clear_all_torrents()
            QMessageBox.information(self, "Done", f"Deleted {count} torrents, page progress reset.")
            self._refresh_db_table()

    # ── Emails tab ──────────────────────────────────────────────

    def _build_emails_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # Import area
        import_group = QGroupBox("Import Emails (login:password, one per line)")
        il = QVBoxLayout(import_group)
        self.email_input = QTextEdit()
        self.email_input.setPlaceholderText(
            "user1@mail.com:password123\n"
            "user2@outlook.com:pass456\n"
            "user3@gmail.com:pass789"
        )
        self.email_input.setMaximumHeight(150)
        il.addWidget(self.email_input)

        btn_row = QHBoxLayout()
        self.btn_import_emails = QPushButton("Import")
        self.btn_import_emails.clicked.connect(self._on_import_emails)
        self.lbl_import_result = QLabel("")
        btn_row.addWidget(self.btn_import_emails)
        btn_row.addWidget(self.lbl_import_result)
        btn_row.addStretch()
        il.addLayout(btn_row)
        layout.addWidget(import_group)

        # Email list table
        list_group = QGroupBox("Email Accounts")
        ll = QVBoxLayout(list_group)

        email_btn_row = QHBoxLayout()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_email_table)
        btn_clear_all = QPushButton("Clear All")
        btn_clear_all.clicked.connect(self._on_clear_all_emails)
        email_btn_row.addWidget(btn_refresh)
        email_btn_row.addWidget(btn_clear_all)
        email_btn_row.addStretch()
        ll.addLayout(email_btn_row)

        self.email_table = QTableWidget(0, 3)
        self.email_table.setHorizontalHeaderLabels(["Email", "Status", "Added"])
        self.email_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.email_table.setEditTriggers(QTableWidget.NoEditTriggers)
        ll.addWidget(self.email_table)

        layout.addWidget(list_group)
        return w

    def _on_import_emails(self):
        text = self.email_input.toPlainText()
        lines = text.strip().split("\n")
        if not lines or not text.strip():
            self.lbl_import_result.setText("Nothing to import")
            return
        added, skipped = self.coordinator.repo.import_emails(lines)
        msg = f"Imported {added} new emails"
        if skipped:
            msg += f" ({skipped} skipped — blocked domain)"
        self.lbl_import_result.setText(msg)
        self.email_input.clear()
        self._refresh_email_table()

    def _on_clear_all_emails(self):
        reply = QMessageBox.question(
            self, "Clear All Emails",
            "Delete ALL email accounts from the database?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            count = self.coordinator.repo.clear_all_emails()
            self.lbl_import_result.setText(f"Deleted {count} emails")
            self._refresh_email_table()

    def _refresh_email_table(self):
        emails = self.coordinator.repo.get_all_emails()
        self.email_table.setRowCount(len(emails))
        for row, e in enumerate(emails):
            email_item = QTableWidgetItem(e["email"])
            status_item = QTableWidgetItem(e["status"])
            date_item = QTableWidgetItem(e["created_at"])

            # Color and strikethrough for used emails
            status = e["status"]
            if status == "exhausted":
                color = QColor(220, 220, 220)
                font = email_item.font()
                font.setStrikeOut(True)
                email_item.setFont(font)
                status_item.setFont(font)
            elif status == "in_use":
                color = QColor(255, 255, 200)
            elif status == "fresh":
                color = QColor(200, 255, 200)
            else:
                color = QColor(255, 200, 200)

            for item in (email_item, status_item, date_item):
                item.setBackground(color)

            self.email_table.setItem(row, 0, email_item)
            self.email_table.setItem(row, 1, status_item)
            self.email_table.setItem(row, 2, date_item)

    # ── Blocked Domains tab ─────────────────────────────────────

    def _build_blocked_domains_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        info = QLabel(
            "Domains automatically blocked when the tracker rejects them during registration.\n"
            "All emails with a blocked domain are marked as banned and skipped by workers."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Table
        self.blocked_table = QTableWidget(0, 3)
        self.blocked_table.setHorizontalHeaderLabels(["Domain", "Reason", "Blocked At"])
        self.blocked_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.blocked_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.blocked_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.blocked_table)

        # Buttons
        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_blocked_table)
        btn_remove = QPushButton("Remove Selected Domain")
        btn_remove.clicked.connect(self._on_remove_blocked_domain)
        btn_row.addWidget(btn_refresh)
        btn_row.addWidget(btn_remove)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._refresh_blocked_table()
        return w

    def _refresh_blocked_table(self):
        domains = self.coordinator.repo.get_blocked_domains()
        self.blocked_table.setRowCount(len(domains))
        for row, d in enumerate(domains):
            self.blocked_table.setItem(row, 0, QTableWidgetItem(d["domain"]))
            self.blocked_table.setItem(row, 1, QTableWidgetItem(d["reason"]))
            self.blocked_table.setItem(row, 2, QTableWidgetItem(d["blocked_at"]))
            color = QColor(255, 220, 220)
            for col in range(3):
                item = self.blocked_table.item(row, col)
                if item:
                    item.setBackground(color)

    def _on_remove_blocked_domain(self):
        row = self.blocked_table.currentRow()
        if row < 0:
            return
        domain_item = self.blocked_table.item(row, 0)
        if not domain_item:
            return
        domain = domain_item.text()
        reply = QMessageBox.question(
            self, "Remove Domain",
            f"Remove '{domain}' from blocklist?\n"
            "(Existing banned emails will NOT be re-enabled automatically)",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.coordinator.repo.remove_blocked_domain(domain)
            self._refresh_blocked_table()

    # ── Pages tab ──────────────────────────────────────────────

    def _build_pages_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # Top bar
        top = QHBoxLayout()
        self.pages_info_label = QLabel("—")
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_pages_grid)
        btn_clear = QPushButton("Clear Page Progress")
        btn_clear.clicked.connect(self._on_clear_pages)

        top.addWidget(self.pages_info_label, 1)
        top.addWidget(btn_refresh)
        top.addWidget(btn_clear)
        layout.addLayout(top)

        # Legend
        legend = QHBoxLayout()
        for color, label in [("#1e1e1e", "Pending"), ("#00c800", "Done"), ("#dcc800", "Scanning")]:
            box = QLabel()
            box.setFixedSize(14, 14)
            box.setStyleSheet(f"background: {color}; border: 1px solid #444;")
            legend.addWidget(box)
            legend.addWidget(QLabel(label))
        legend.addStretch()
        layout.addLayout(legend)

        # Grid inside scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.page_grid = PageGridWidget()
        scroll.setWidget(self.page_grid)
        scroll.setStyleSheet("background: #111;")
        layout.addWidget(scroll, 1)

        self._refresh_pages_grid()
        return w

    def _refresh_pages_grid(self):
        from ..config_manager import load_config
        cfg = load_config()
        start = cfg["tracker"]["pages_start"]
        end = cfg["tracker"]["pages_end"]
        cat = cfg["tracker"]["category_id"]
        total = max(end - start + 1, 0)

        statuses = self.coordinator.repo.get_page_statuses(cat) if cat else {}
        completed = sum(1 for s in statuses.values() if s == "completed")
        in_progress = sum(1 for s in statuses.values() if s == "in_progress")

        self.page_grid.set_range(start, end)
        self.page_grid.set_statuses(statuses)
        self.pages_info_label.setText(
            f"Pages {start}–{end} ({total} total)  |  "
            f"Done: {completed}  |  Scanning: {in_progress}  |  "
            f"Remaining: {total - completed - in_progress}"
        )

    def _on_clear_pages(self):
        from ..config_manager import load_config
        cfg = load_config()
        cat = cfg["tracker"]["category_id"]
        if not cat:
            QMessageBox.warning(self, "Error", "No category ID configured.")
            return
        reply = QMessageBox.question(
            self, "Clear Page Progress",
            "Reset all page progress? Parser will re-scan all pages.\n"
            "(Torrents in DB will NOT be deleted)",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            count = self.coordinator.repo.clear_page_progress(cat)
            QMessageBox.information(self, "Done", f"Cleared {count} page records.")
            self._refresh_pages_grid()

    # ── Downloader tab ─────────────────────────────────────────

    def _build_downloader_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # Controls
        ctrl = QHBoxLayout()
        self.dl_btn_start = QPushButton("Start Downloads")
        self.dl_btn_start.clicked.connect(self._on_start_downloads)
        self.dl_btn_stop = QPushButton("Stop")
        self.dl_btn_stop.clicked.connect(self._on_stop_downloads)
        self.dl_btn_stop.setEnabled(False)

        ctrl.addWidget(self.dl_btn_start)
        ctrl.addWidget(self.dl_btn_stop)

        ctrl.addWidget(QLabel("  Workers:"))
        self.dl_worker_count = QSpinBox()
        self.dl_worker_count.setRange(1, 10)
        self.dl_worker_count.setValue(1)
        ctrl.addWidget(self.dl_worker_count)

        self.dl_share_email = QCheckBox("Share 1 email")
        self.dl_share_email.setChecked(True)
        ctrl.addWidget(self.dl_share_email)

        ctrl.addWidget(QLabel("  Max/account:"))
        self.dl_max_per = QSpinBox()
        self.dl_max_per.setRange(1, 500)
        self.dl_max_per.setValue(50)
        ctrl.addWidget(self.dl_max_per)

        ctrl.addStretch()
        layout.addLayout(ctrl)

        # Stats
        self.dl_stats_label = QLabel("Ready — configure and press Start")
        self.dl_stats_label.setWordWrap(True)
        self.dl_stats_label.setStyleSheet("font-size: 13px; padding: 6px;")
        layout.addWidget(self.dl_stats_label)

        # Progress bar
        dl_progress_row = QHBoxLayout()
        self.dl_progress_label = QLabel("Progress:")
        self.dl_progress_bar = QProgressBar()
        self.dl_progress_bar.setTextVisible(True)
        self.dl_progress_bar.setFormat("%v / %m  (%p%)")
        dl_progress_row.addWidget(self.dl_progress_label)
        dl_progress_row.addWidget(self.dl_progress_bar, 1)
        layout.addLayout(dl_progress_row)

        # Workers table
        self.dl_workers_table = QTableWidget(0, 3)
        self.dl_workers_table.setHorizontalHeaderLabels(["Worker", "State", "Status"])
        self.dl_workers_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.dl_workers_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.dl_workers_table)

        # Log
        self.dl_log = QPlainTextEdit()
        self.dl_log.setReadOnly(True)
        self.dl_log.setMaximumBlockCount(3000)
        self.dl_log.setMaximumHeight(180)
        layout.addWidget(self.dl_log)

        # Refresh timer for download stats
        self.dl_timer = QTimer(self)
        self.dl_timer.timeout.connect(self._refresh_dl_stats)
        self.dl_timer.start(2000)

        # Status signal for download workers
        self.dl_status_signal = StatusSignal()
        self.dl_status_signal.updated.connect(self._on_dl_worker_status)
        self.coordinator.set_dl_status_callback(
            lambda wid, state, msg: self.dl_status_signal.updated.emit(wid, state, msg)
        )

        # Load config values
        cfg = load_config()
        dl_cfg = cfg.get("downloader", {})
        self.dl_worker_count.setValue(dl_cfg.get("worker_count", 1))
        self.dl_share_email.setChecked(dl_cfg.get("share_email", True))
        self.dl_max_per.setValue(dl_cfg.get("max_per_account", 50))

        return w

    def _on_start_downloads(self):
        # Save downloader config
        cfg = load_config()
        cfg["downloader"] = {
            "worker_count": self.dl_worker_count.value(),
            "share_email": self.dl_share_email.isChecked(),
            "max_per_account": self.dl_max_per.value(),
        }
        save_config(cfg)

        self.dl_btn_start.setEnabled(False)
        self.dl_btn_stop.setEnabled(True)
        self.coordinator.start_downloads(self.dl_worker_count.value())

    def _on_stop_downloads(self):
        self.dl_btn_stop.setEnabled(False)
        self.coordinator.stop_downloads()
        self.dl_btn_start.setEnabled(True)

    def _on_dl_worker_status(self, worker_id: str, state: str, message: str):
        """Update download workers table + log."""
        # Log every status message
        self.dl_log.appendPlainText(f"[{worker_id}] {message}")

        for row in range(self.dl_workers_table.rowCount()):
            if self.dl_workers_table.item(row, 0) and self.dl_workers_table.item(row, 0).text() == worker_id:
                self.dl_workers_table.setItem(row, 1, QTableWidgetItem(state))
                self.dl_workers_table.setItem(row, 2, QTableWidgetItem(message))
                color = {"running": QColor(200, 255, 200), "error": QColor(255, 200, 200),
                         "stopped": QColor(220, 220, 220)}.get(state)
                if color:
                    for col in range(3):
                        item = self.dl_workers_table.item(row, col)
                        if item:
                            item.setBackground(color)
                return

        row = self.dl_workers_table.rowCount()
        self.dl_workers_table.insertRow(row)
        self.dl_workers_table.setItem(row, 0, QTableWidgetItem(worker_id))
        self.dl_workers_table.setItem(row, 1, QTableWidgetItem(state))
        self.dl_workers_table.setItem(row, 2, QTableWidgetItem(message))

    def _refresh_dl_stats(self):
        if not self.coordinator.is_dl_running:
            return
        try:
            stats = self.coordinator.get_download_stats()
            total = stats["total_with_url"]
            downloaded = stats["downloaded"]
            pending = stats["pending"]
            downloading = stats["downloading"]

            self.dl_stats_label.setText(
                f"Total with URL: {total}  |  "
                f"Downloaded: {downloaded}  |  "
                f"In progress: {downloading}  |  "
                f"Pending: {pending}  |  "
                f"Session: +{stats.get('session_downloaded', 0)} done, "
                f"{stats.get('session_errors', 0)} errors"
            )

            self.dl_progress_bar.setMaximum(max(total, 1))
            self.dl_progress_bar.setValue(downloaded)
        except Exception:
            pass

    # ── Config tab ──────────────────────────────────────────────

    def _build_config_tab(self) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer_layout.addWidget(scroll)

        w = QWidget()
        layout = QVBoxLayout(w)
        scroll.setWidget(w)

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
        self.cfg_skip_reg = QCheckBox("Skip registration — login directly with email credentials")
        self.cfg_skip_reg.setToolTip(
            "When enabled, the email list is treated as tracker logins (login:password).\n"
            "No registration will be performed — workers log in directly."
        )
        self.cfg_share_email = QCheckBox("Share 1 email across all workers (don't expire after use)")
        self.cfg_share_email.setToolTip(
            "All workers use the first email in the list.\n"
            "The email is never marked as exhausted — stays fresh forever."
        )
        tl.addRow("Tracker:", self.cfg_tracker_profile)
        tl.addRow("", self.cfg_tracker_url_label)
        tl.addRow("Category ID:", self.cfg_category_id)
        tl.addRow("Pages start:", self.cfg_pages_start)
        tl.addRow("Pages end:", self.cfg_pages_end)
        tl.addRow("Max downloads/account:", self.cfg_max_dl)
        tl.addRow("", self.cfg_skip_reg)
        tl.addRow("", self.cfg_share_email)
        layout.addWidget(tracker_group)

        # Workers settings
        workers_group = QGroupBox("Workers")
        wl = QFormLayout(workers_group)
        self.cfg_parser_count = QSpinBox(); self.cfg_parser_count.setRange(1, 20)
        self.cfg_download_dir = QLineEdit()
        wl.addRow("Parser workers:", self.cfg_parser_count)
        wl.addRow("Download dir:", self.cfg_download_dir)
        layout.addWidget(workers_group)

        # Format filters
        fmt_group = QGroupBox("Format Filters")
        fmt_layout = QFormLayout(fmt_group)
        self.cfg_format_filters = QLineEdit()
        self.cfg_format_filters.setPlaceholderText("Comma-separated: 4K, 2160p, UHD, 8K (empty = accept all)")
        self.cfg_format_filters.setToolTip(
            "Only save torrents whose title contains one of these video formats.\n"
            "Duplicates like 4K and 2160p are auto-deduplicated.\n"
            "Leave empty to save all torrents regardless of format."
        )
        fmt_layout.addRow("Formats:", self.cfg_format_filters)
        layout.addWidget(fmt_group)

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

        # Telegram CAPTCHA
        tg_group = QGroupBox("Telegram (CAPTCHA Solver)")
        tg_layout = QFormLayout(tg_group)
        self.cfg_tg_token = QLineEdit()
        self.cfg_tg_token.setPlaceholderText("Bot token from @BotFather")
        self.cfg_tg_token.setEchoMode(QLineEdit.Password)
        self.cfg_tg_chat_id = QLineEdit()
        self.cfg_tg_chat_id.setPlaceholderText("Your chat ID (get from @userinfobot)")
        tg_layout.addRow("Bot Token:", self.cfg_tg_token)
        tg_layout.addRow("Chat ID:", self.cfg_tg_chat_id)
        layout.addWidget(tg_group)

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
        return outer

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
        self.cfg_skip_reg.setChecked(cfg["tracker"].get("skip_registration", False))
        self.cfg_share_email.setChecked(cfg["tracker"].get("share_email", False))

        self.cfg_parser_count.setValue(cfg["workers"]["parser_count"])
        # email_reg_count removed — parser workers handle registration themselves
        self.cfg_download_dir.setText(cfg["workers"]["download_dir"])

        fmt = cfg["tags"].get("format_filters", cfg["tags"].get("download_tags", []))
        self.cfg_format_filters.setText(", ".join(fmt))

        proxies = cfg["proxy"].get("list", [])
        self.cfg_proxy_list.setPlainText("\n".join(proxies))

        nl = cfg.get("notletters", {})
        self.cfg_nl_token.setText(nl.get("api_token", ""))
        self.cfg_nl_email_type.setCurrentIndex(nl.get("email_type", 0))
        self.cfg_nl_batch.setValue(nl.get("batch_size", 3))

        tg = cfg.get("telegram", {})
        self.cfg_tg_token.setText(tg.get("bot_token", ""))
        self.cfg_tg_chat_id.setText(tg.get("chat_id", ""))

    def _ui_to_config(self) -> dict:
        proxy_lines = [l.strip() for l in self.cfg_proxy_list.toPlainText().strip().split("\n") if l.strip()]
        return {
            "tracker": {
                "profile": self.cfg_tracker_profile.currentData() or "rutracker",
                "category_id": self.cfg_category_id.text().strip(),
                "pages_start": self.cfg_pages_start.value(),
                "pages_end": self.cfg_pages_end.value(),
                "max_downloads_per_account": self.cfg_max_dl.value(),
                "skip_registration": self.cfg_skip_reg.isChecked(),
                "share_email": self.cfg_share_email.isChecked(),
            },
            "workers": {
                "parser_count": self.cfg_parser_count.value(),
                "download_dir": self.cfg_download_dir.text().strip() or "downloads",
            },
            "tags": {
                "format_filters": [t.strip() for t in self.cfg_format_filters.text().split(",") if t.strip()],
                "record_tags": [],
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
            "telegram": {
                "bot_token": self.cfg_tg_token.text().strip(),
                "chat_id": self.cfg_tg_chat_id.text().strip(),
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
            pages_done = stats['pages_completed']
            pages_wip = stats.get('pages_in_progress', 0)
            pages_total = stats.get('pages_total', 0)

            elapsed = stats.get('elapsed', 0)
            avg_page = stats.get('avg_page_time', 0)
            eta = stats.get('eta', 0)
            elapsed_str = _fmt_duration(elapsed) if elapsed > 0 else "—"
            avg_str = f"{avg_page:.0f}s" if avg_page > 0 else "—"
            eta_str = _fmt_duration(eta) if eta > 0 else "—"

            text = (
                f"DB: {stats['total_torrents']} torrents  |  "
                f"Saved: {stats.get('worker_saved', 0)}, "
                f"Filtered: {stats.get('worker_filtered', 0)}, "
                f"Dupes: {stats.get('worker_duplicate', 0)}, "
                f"Errors: {stats.get('worker_error', 0)}  |  "
                f"Time: {elapsed_str} (avg {avg_str}/page)  |  "
                f"ETA: {eta_str}  |  "
                f"RAM: {stats.get('memory_mb', 0):.0f} MB"
            )
            self.lbl_stats.setText(text)

            # Update page progress bar
            self.page_progress.setMaximum(max(pages_total, 1))
            self.page_progress.setValue(pages_done)
            self.lbl_pages.setText(
                f"Pages: {pages_done} done"
                + (f", {pages_wip} scanning" if pages_wip else "")
                + f"  —  ETA: {eta_str}"
            )

            # Auto-refresh tables every cycle
            self._refresh_email_table()
            self._refresh_blocked_table()
            self._refresh_db_table()
            self._refresh_pages_grid()
        except Exception:
            pass

    def closeEvent(self, event):
        running = self.coordinator.is_running or self.coordinator.is_dl_running
        if running:
            reply = QMessageBox.question(
                self, "Quit", "Workers are running. Stop them and quit?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            self.coordinator.stop()
            self.coordinator.stop_downloads()
        event.accept()


class PageGridWidget(QWidget):
    """Visual grid of page squares: black=pending, green=done, yellow=in-progress."""

    CELL = 18       # square size in px
    GAP = 2         # gap between squares
    COLS = 40       # squares per row

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pages_start = 1
        self._pages_end = 1
        self._statuses: dict[int, str] = {}  # page_num -> 'completed'|'in_progress'
        self.setMinimumHeight(50)
        self._tooltip_page: int | None = None
        self.setMouseTracking(True)

    def set_range(self, start: int, end: int):
        self._pages_start = start
        self._pages_end = end
        self._update_size()
        self.update()

    def set_statuses(self, statuses: dict[int, str]):
        self._statuses = statuses
        self.update()

    def _update_size(self):
        total = max(self._pages_end - self._pages_start + 1, 0)
        rows = (total + self.COLS - 1) // self.COLS if total else 1
        h = rows * (self.CELL + self.GAP) + self.GAP + 4
        self.setMinimumHeight(h)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        total = max(self._pages_end - self._pages_start + 1, 0)
        completed = sum(1 for s in self._statuses.values() if s == "completed")

        # Draw cells
        for i in range(total):
            page_num = self._pages_start + i
            col = i % self.COLS
            row = i // self.COLS
            x = self.GAP + col * (self.CELL + self.GAP)
            y = self.GAP + row * (self.CELL + self.GAP)

            status = self._statuses.get(page_num, "")
            if status == "completed":
                painter.setBrush(QBrush(QColor(0, 200, 0)))
            elif status == "in_progress":
                painter.setBrush(QBrush(QColor(220, 200, 0)))
            else:
                painter.setBrush(QBrush(QColor(30, 30, 30)))

            painter.setPen(QPen(QColor(60, 60, 60), 1))
            painter.drawRect(x, y, self.CELL, self.CELL)

        painter.end()

    def mouseMoveEvent(self, event):
        pos = event.position() if hasattr(event, 'position') else event.pos()
        col = int((pos.x() - self.GAP) / (self.CELL + self.GAP))
        row = int((pos.y() - self.GAP) / (self.CELL + self.GAP))
        if col < 0 or col >= self.COLS or row < 0:
            self.setToolTip("")
            return
        idx = row * self.COLS + col
        page_num = self._pages_start + idx
        if page_num > self._pages_end:
            self.setToolTip("")
            return
        status = self._statuses.get(page_num, "pending")
        self.setToolTip(f"Page {page_num} — {status}")


class QtLogHandler(logging.Handler):
    """Thread-safe logging handler that writes to a QPlainTextEdit via signal."""
    def __init__(self, text_widget: QPlainTextEdit):
        super().__init__()
        self.text_widget = text_widget
        self._signal = _LogSignal()
        self._signal.message.connect(self._append)

    def _append(self, msg: str):
        try:
            self.text_widget.appendPlainText(msg)
        except RuntimeError:
            pass  # widget might be deleted

    def emit(self, record):
        msg = self.format(record)
        try:
            self._signal.message.emit(msg)
        except RuntimeError:
            pass


class _LogSignal(QObject):
    message = Signal(str)
