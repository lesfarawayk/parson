"""Entry point for the Parson torrent parser application."""

import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from PySide6.QtWidgets import QApplication
from src.gui.main_window import MainWindow

LOG_DIR = Path(__file__).resolve().parent / "data" / "logs"


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Console handler
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # File handler — rotates at 5 MB, keeps last 5 files
    file_handler = RotatingFileHandler(
        LOG_DIR / "parson.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.getLogger().addHandler(file_handler)

    app = QApplication(sys.argv)
    app.setApplicationName("Parson")
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
