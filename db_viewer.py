"""
Parson DB Viewer — standalone program for browsing the torrent database.
No dependencies beyond PySide6 and sqlite3 (built-in).
Run: python db_viewer.py
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QLabel,
    QPushButton, QLineEdit, QGroupBox, QPlainTextEdit, QSplitter,
    QFileDialog, QMessageBox, QAbstractItemView,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap, QFont

DB_DEFAULT = Path(__file__).resolve().parent / "data" / "parson.db"

COLUMNS = [
    ("id", "ID", 50),
    ("topic_id", "Topic", 70),
    ("studio", "Studio", 120),
    ("actors", "Actors", 150),
    ("film_name", "Film Name", 220),
    ("year", "Year", 45),
    ("formats", "Formats", 90),
    ("tags", "Tags", 160),
    ("devices", "Devices", 70),
    ("duration", "Duration", 65),
    ("file_size", "Size", 65),
    ("seeds", "Seeds", 45),
    ("peers", "Peers", 45),
    ("status", "Status", 70),
]

STATUS_COLORS = {
    "parsed":      QColor(200, 230, 255),
    "approved":    QColor(200, 255, 200),
    "rejected":    QColor(255, 220, 220),
    "skipped":     QColor(240, 240, 240),
    "error":       QColor(255, 180, 180),
    "downloading": QColor(255, 255, 200),
    "downloaded":  QColor(180, 255, 180),
    "found":       QColor(230, 230, 230),
}


def _json_pretty(raw: str) -> str:
    """Parse JSON list and join with commas. Falls back to raw string."""
    if not raw or raw == "[]":
        return ""
    try:
        items = json.loads(raw)
        if isinstance(items, list):
            return ", ".join(str(i) for i in items)
    except Exception:
        pass
    return raw


class DBViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Parson DB Viewer")
        self.setMinimumSize(1100, 650)
        self.db_path: Path | None = None
        self.conn: sqlite3.Connection | None = None
        self._rows: list[dict] = []
        self._build_ui()

        # Auto-open default DB if exists
        if DB_DEFAULT.exists():
            self._open_db(DB_DEFAULT)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # --- Top bar ---
        top = QHBoxLayout()
        self.lbl_path = QLabel("No database loaded")
        self.lbl_path.setStyleSheet("color: #666;")
        btn_open = QPushButton("Open DB...")
        btn_open.clicked.connect(self._on_open)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._load_data)
        top.addWidget(btn_open)
        top.addWidget(btn_refresh)
        top.addWidget(self.lbl_path, 1)
        root.addLayout(top)

        # --- Filters ---
        filt = QHBoxLayout()
        filt.addWidget(QLabel("Status:"))
        self.filter_status = QComboBox()
        self.filter_status.addItem("All", "")
        for s in ["parsed", "approved", "rejected", "found", "skipped",
                   "downloading", "downloaded", "error"]:
            self.filter_status.addItem(s.capitalize(), s)
        self.filter_status.currentIndexChanged.connect(self._load_data)

        filt.addWidget(self.filter_status)

        filt.addWidget(QLabel("  Search:"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter by studio, name, tags...")
        self.search_box.textChanged.connect(self._apply_search)
        filt.addWidget(self.search_box, 1)

        self.lbl_count = QLabel("")
        filt.addWidget(self.lbl_count)
        filt.addStretch()

        btn_clear = QPushButton("Clear Database")
        btn_clear.clicked.connect(self._on_clear_db)
        filt.addWidget(btn_clear)

        root.addLayout(filt)

        # --- Splitter: table + details ---
        splitter = QSplitter(Qt.Vertical)

        # Table
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        for i, (_, _, w) in enumerate(COLUMNS):
            self.table.setColumnWidth(i, w)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)  # Film Name
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)  # Tags
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.currentCellChanged.connect(self._on_row_selected)
        splitter.addWidget(self.table)

        # Detail panel
        detail_w = QWidget()
        detail_layout = QHBoxLayout(detail_w)
        detail_layout.setContentsMargins(4, 4, 4, 4)

        # Cover image
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(180, 240)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setStyleSheet("background: #222; border: 1px solid #555;")
        self.cover_label.setText("No cover")
        self.cover_label.setStyleSheet(
            "background: #222; border: 1px solid #555; color: #888; font-size: 11px;"
        )
        detail_layout.addWidget(self.cover_label)

        # Text details
        self.detail_text = QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.Monospace)
        self.detail_text.setFont(mono)
        detail_layout.addWidget(self.detail_text, 1)

        splitter.addWidget(detail_w)
        splitter.setSizes([500, 200])
        root.addWidget(splitter)

    # ── DB operations ──────────────────────────────────────────

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Database", str(DB_DEFAULT.parent), "SQLite (*.db *.sqlite);;All (*)"
        )
        if path:
            self._open_db(Path(path))

    def _open_db(self, path: Path):
        if self.conn:
            self.conn.close()
        try:
            self.conn = sqlite3.connect(str(path))
            self.conn.row_factory = sqlite3.Row
            self.db_path = path
            self.lbl_path.setText(str(path))
            self.lbl_path.setStyleSheet("color: #080;")
            self._load_data()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Cannot open database:\n{e}")

    def _load_data(self):
        if not self.conn:
            return
        status = self.filter_status.currentData()
        try:
            if status:
                cur = self.conn.execute(
                    "SELECT * FROM torrents WHERE status = ? ORDER BY id DESC LIMIT 5000",
                    (status,),
                )
            else:
                cur = self.conn.execute(
                    "SELECT * FROM torrents ORDER BY id DESC LIMIT 5000"
                )
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
            self._rows = [dict(zip(col_names, row)) for row in rows]
        except Exception as e:
            self._rows = []
            self.lbl_count.setText(f"Error: {e}")
            return

        self._apply_search()

    def _apply_search(self):
        query = self.search_box.text().strip().lower()
        if query:
            filtered = []
            for r in self._rows:
                searchable = " ".join([
                    str(r.get("studio", "")),
                    str(r.get("actors", "")),
                    str(r.get("film_name", "")),
                    str(r.get("tags", "")),
                    str(r.get("title_raw", "")),
                    str(r.get("formats", "")),
                    str(r.get("devices", "")),
                ]).lower()
                if query in searchable:
                    filtered.append(r)
        else:
            filtered = self._rows

        self.lbl_count.setText(f"{len(filtered)} / {len(self._rows)} records")
        self._fill_table(filtered)

    def _fill_table(self, rows: list[dict]):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        self._visible_rows = rows

        for row_idx, r in enumerate(rows):
            for col_idx, (key, _, _) in enumerate(COLUMNS):
                raw = r.get(key, "")
                if raw is None:
                    raw = ""
                # Pretty-print JSON columns
                if key in ("formats", "tags", "devices", "actors"):
                    text = _json_pretty(str(raw))
                else:
                    text = str(raw)

                item = QTableWidgetItem(text)
                # Numeric sort for numeric columns
                if key in ("id", "seeds", "peers", "page_number"):
                    item.setData(Qt.DisplayRole, text)
                    try:
                        item.setData(Qt.UserRole, int(raw) if raw else 0)
                    except (ValueError, TypeError):
                        pass

                color = STATUS_COLORS.get(str(r.get("status", "")))
                if color:
                    item.setBackground(color)
                self.table.setItem(row_idx, col_idx, item)

        self.table.setSortingEnabled(True)

    def _on_row_selected(self, row, col, prev_row, prev_col):
        if not hasattr(self, "_visible_rows") or row < 0 or row >= len(self._visible_rows):
            self.detail_text.clear()
            return
        r = self._visible_rows[row]

        # Cover image
        cover_path = r.get("cover_path", "")
        if cover_path and Path(cover_path).exists():
            pix = QPixmap(str(cover_path))
            if not pix.isNull():
                self.cover_label.setPixmap(
                    pix.scaled(180, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            else:
                self.cover_label.setText("Bad image")
        else:
            self.cover_label.clear()
            self.cover_label.setText("No cover")

        # Detail text
        lines = [
            f"{'Topic ID:':<16} {r.get('topic_id', '')}",
            f"{'Title raw:':<16} {r.get('title_raw', '')}",
            f"",
            f"{'Studio:':<16} {r.get('studio', '')}",
            f"{'Actors:':<16} {_json_pretty(str(r.get('actors', '')))}",
            f"{'Film:':<16} {r.get('film_name', '')}",
            f"{'Year:':<16} {r.get('year', '')}",
            f"{'Duration:':<16} {r.get('duration', '')}",
            f"{'Size:':<16} {r.get('file_size', '')}",
            f"",
            f"{'Formats:':<16} {_json_pretty(str(r.get('formats', '')))}",
            f"{'Tags:':<16} {_json_pretty(str(r.get('tags', '')))}",
            f"{'Devices:':<16} {_json_pretty(str(r.get('devices', '')))}",
            f"",
            f"{'Seeds:':<16} {r.get('seeds', 0)}",
            f"{'Peers:':<16} {r.get('peers', 0)}",
            f"{'Status:':<16} {r.get('status', '')}",
            f"{'Page:':<16} {r.get('page_number', '')}",
            f"{'Category:':<16} {r.get('category_id', '')}",
            f"",
            f"{'Download URL:':<16} {r.get('download_url', '')}",
            f"{'Cover URL:':<16} {r.get('cover_url', '')}",
            f"{'Cover path:':<16} {r.get('cover_path', '')}",
            f"",
            f"--- Description ---",
            str(r.get("description", ""))[:2000],
        ]
        self.detail_text.setPlainText("\n".join(lines))

    def _on_clear_db(self):
        if not self.conn:
            return
        reply = QMessageBox.question(
            self, "Clear Database",
            "Delete ALL parsed torrents and reset page progress?\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                self.conn.execute("DELETE FROM torrents")
                self.conn.execute("DELETE FROM page_progress")
                self.conn.commit()
                QMessageBox.information(self, "Done", "Database cleared.")
                self._load_data()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed: {e}")

    def closeEvent(self, event):
        if self.conn:
            self.conn.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = DBViewer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
