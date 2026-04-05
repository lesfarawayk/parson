"""
Parson DB Editor — standalone program for browsing and editing the torrent database.
No dependencies beyond PySide6 and sqlite3 (built-in).
Run: python db_viewer.py
"""

import json
import re
import sqlite3
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QLabel,
    QPushButton, QLineEdit, QPlainTextEdit, QSplitter,
    QFileDialog, QMessageBox, QAbstractItemView,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap, QFont, QClipboard

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
    ("file_size", "Size", 80),
    ("seeds", "Seeds", 50),
    ("peers", "Peers", 50),
    ("status", "Status", 70),
    ("download_url", "Download URL", 150),
    ("description", "Description", 200),
    ("has_torrent", ".torrent", 55),
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
    if not raw or raw == "[]":
        return ""
    try:
        items = json.loads(raw)
        if isinstance(items, list):
            return ", ".join(str(i) for i in items)
    except Exception:
        pass
    return raw


def _parse_size_bytes(size_str: str) -> float:
    """Parse '40.73 GB' / '512 MB' / '1.2 TB' into bytes for sorting."""
    if not size_str:
        return 0
    m = re.match(r"([\d.]+)\s*(TB|GB|MB|KB|B)", str(size_str).strip(), re.IGNORECASE)
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2).upper()
    multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    return val * multipliers.get(unit, 0)


def _parse_duration_seconds(dur_str: str) -> int:
    """Parse '01:02:05' or '31:52' into total seconds for sorting."""
    if not dur_str:
        return 0
    parts = str(dur_str).strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        else:
            return int(parts[0])
    except (ValueError, IndexError):
        return 0


class SortableItem(QTableWidgetItem):
    """Table item that sorts by a numeric sort_value instead of text."""
    def __init__(self, text: str, sort_value=None):
        super().__init__(text)
        self._sort_value = sort_value if sort_value is not None else text

    def __lt__(self, other):
        if isinstance(other, SortableItem):
            try:
                return self._sort_value < other._sort_value
            except TypeError:
                return str(self._sort_value) < str(other._sort_value)
        return super().__lt__(other)


class DBEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Parson DB Editor")
        self.setMinimumSize(1100, 650)
        self.db_path: Path | None = None
        self.conn: sqlite3.Connection | None = None
        self._rows: list[dict] = []
        self._build_ui()

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

        btn_delete_sel = QPushButton("Delete Selected")
        btn_delete_sel.clicked.connect(self._on_delete_selected)
        filt.addWidget(btn_delete_sel)

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
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.currentCellChanged.connect(self._on_row_selected)
        splitter.addWidget(self.table)

        # Detail panel
        detail_w = QWidget()
        detail_layout = QHBoxLayout(detail_w)
        detail_layout.setContentsMargins(4, 4, 4, 4)

        # Left: cover + action buttons
        left_panel = QVBoxLayout()
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(180, 240)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setStyleSheet(
            "background: #222; border: 1px solid #555; color: #888; font-size: 11px;"
        )
        self.cover_label.setText("No cover")
        left_panel.addWidget(self.cover_label)

        btn_copy_desc = QPushButton("Copy Description")
        btn_copy_desc.clicked.connect(self._on_copy_description)
        left_panel.addWidget(btn_copy_desc)

        btn_copy_url = QPushButton("Copy Download URL")
        btn_copy_url.clicked.connect(self._on_copy_download_url)
        left_panel.addWidget(btn_copy_url)

        btn_save_torrent = QPushButton("Save .torrent")
        btn_save_torrent.clicked.connect(self._on_save_torrent_file)
        left_panel.addWidget(btn_save_torrent)

        left_panel.addStretch()
        detail_layout.addLayout(left_panel)

        self.detail_text = QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.Monospace)
        self.detail_text.setFont(mono)
        detail_layout.addWidget(self.detail_text, 1)

        splitter.addWidget(detail_w)
        splitter.setSizes([500, 280])
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

    def _get_light_columns(self) -> str:
        """Column list for main query — excludes heavy BLOBs."""
        try:
            cur = self.conn.execute("PRAGMA table_info(torrents)")
            all_cols = [row[1] for row in cur.fetchall()]
            skip = {"cover_data", "torrent_file"}
            cols = [c for c in all_cols if c not in skip]
            # Add virtual has_torrent flag: 1 if torrent_file is not null and length > 0
            return ", ".join(cols) + ", (torrent_file IS NOT NULL AND length(torrent_file) > 0) AS has_torrent_flag"
        except Exception:
            return "*"

    def _load_data(self):
        if not self.conn:
            return
        status = self.filter_status.currentData()
        cols = self._get_light_columns()
        try:
            if status:
                cur = self.conn.execute(
                    f"SELECT {cols} FROM torrents WHERE status = ? ORDER BY id DESC",
                    (status,),
                )
            else:
                cur = self.conn.execute(
                    f"SELECT {cols} FROM torrents ORDER BY id DESC"
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
        self._rows_by_id = {str(r.get("topic_id", r.get("id", ""))): r for r in rows}

        for row_idx, r in enumerate(rows):
            for col_idx, (key, _, _) in enumerate(COLUMNS):
                # Virtual column: has_torrent
                if key == "has_torrent":
                    text = "Yes" if r.get("has_torrent_flag") else ""
                    item = QTableWidgetItem(text)
                    if text:
                        item.setBackground(QColor(180, 255, 180))
                elif key == "description":
                    raw_desc = str(r.get("description", "") or "")
                    text = raw_desc[:80].replace("\n", " ") + ("..." if len(raw_desc) > 80 else "")
                    item = QTableWidgetItem(text)
                else:
                    raw = r.get(key, "")
                    if raw is None:
                        raw = ""

                    if key in ("formats", "tags", "devices", "actors"):
                        text = _json_pretty(str(raw))
                    else:
                        text = str(raw)

                    if key in ("id", "seeds", "peers"):
                        try:
                            sort_val = int(raw) if raw else 0
                        except (ValueError, TypeError):
                            sort_val = 0
                        item = SortableItem(text, sort_val)
                    elif key == "file_size":
                        item = SortableItem(text, _parse_size_bytes(text))
                    elif key == "duration":
                        item = SortableItem(text, _parse_duration_seconds(text))
                    elif key == "year":
                        try:
                            sort_val = int(raw) if raw else 0
                        except (ValueError, TypeError):
                            sort_val = 0
                        item = SortableItem(text, sort_val)
                    else:
                        item = QTableWidgetItem(text)

                color = STATUS_COLORS.get(str(r.get("status", "")))
                if color:
                    item.setBackground(color)
                self.table.setItem(row_idx, col_idx, item)

        self.table.setSortingEnabled(True)

    def _on_row_selected(self, row, col, prev_row, prev_col):
        if row < 0:
            self.detail_text.clear()
            return
        topic_item = self.table.item(row, 1)
        if not topic_item or not hasattr(self, "_rows_by_id"):
            self.detail_text.clear()
            return
        r = self._rows_by_id.get(topic_item.text())
        if not r:
            self.detail_text.clear()
            return

        # Cover image — load BLOB from DB on demand, fallback to file path
        cover_loaded = False
        if self.conn:
            try:
                cur = self.conn.execute(
                    "SELECT cover_data FROM torrents WHERE topic_id = ?",
                    (r.get("topic_id", ""),)
                )
                row_data = cur.fetchone()
                cover_data = row_data[0] if row_data else None
                if cover_data and isinstance(cover_data, (bytes, bytearray)):
                    pix = QPixmap()
                    pix.loadFromData(cover_data)
                    if not pix.isNull():
                        self.cover_label.setPixmap(
                            pix.scaled(180, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        )
                        cover_loaded = True
            except Exception:
                pass

        if not cover_loaded:
            cover_path = r.get("cover_path", "")
            if cover_path and Path(cover_path).exists():
                pix = QPixmap(str(cover_path))
                if not pix.isNull():
                    self.cover_label.setPixmap(
                        pix.scaled(180, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )
                    cover_loaded = True

        if not cover_loaded:
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

    # ── Clipboard / export ─────────────────────────────────────

    def _get_selected_row_data(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        topic_item = self.table.item(row, 1)
        if not topic_item or not hasattr(self, "_rows_by_id"):
            return None
        return self._rows_by_id.get(topic_item.text())

    def _on_copy_description(self):
        r = self._get_selected_row_data()
        if not r:
            QMessageBox.information(self, "Info", "Select a row first.")
            return
        desc = str(r.get("description", "") or "")
        if not desc:
            QMessageBox.information(self, "Info", "No description for this entry.")
            return
        QApplication.clipboard().setText(desc)
        self.statusBar().showMessage(f"Description copied ({len(desc)} chars)", 3000)

    def _on_copy_download_url(self):
        r = self._get_selected_row_data()
        if not r:
            QMessageBox.information(self, "Info", "Select a row first.")
            return
        url = str(r.get("download_url", "") or "")
        if not url:
            QMessageBox.information(self, "Info", "No download URL for this entry.")
            return
        QApplication.clipboard().setText(url)
        self.statusBar().showMessage(f"Download URL copied", 3000)

    def _on_save_torrent_file(self):
        r = self._get_selected_row_data()
        if not r:
            QMessageBox.information(self, "Info", "Select a row first.")
            return
        topic_id = r.get("topic_id", "unknown")
        # Load torrent_file BLOB on demand
        tf = None
        if self.conn:
            try:
                cur = self.conn.execute(
                    "SELECT torrent_file FROM torrents WHERE topic_id = ?", (topic_id,)
                )
                row_data = cur.fetchone()
                tf = row_data[0] if row_data else None
            except Exception:
                pass
        if not tf or not isinstance(tf, (bytes, bytearray)) or len(tf) == 0:
            QMessageBox.information(self, "Info", "No .torrent file stored for this entry.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save .torrent", f"{topic_id}.torrent", "Torrent files (*.torrent)"
        )
        if path:
            Path(path).write_bytes(tf)
            self.statusBar().showMessage(f"Saved: {path}", 3000)

    # ── Edit operations ───────────────────────────────────────

    def _on_delete_selected(self):
        if not self.conn:
            return
        rows = set(idx.row() for idx in self.table.selectedIndexes())
        if not rows:
            return
        topic_ids = []
        for row in rows:
            item = self.table.item(row, 1)  # topic_id column
            if item:
                topic_ids.append(item.text())
        if not topic_ids:
            return

        reply = QMessageBox.question(
            self, "Delete Selected",
            f"Delete {len(topic_ids)} selected torrents from the database?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                placeholders = ",".join("?" for _ in topic_ids)
                self.conn.execute(
                    f"DELETE FROM torrents WHERE topic_id IN ({placeholders})",
                    topic_ids,
                )
                self.conn.commit()
                self._load_data()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed: {e}")

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
    window = DBEditor()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
