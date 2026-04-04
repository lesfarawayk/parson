"""Entry point for the Parson torrent parser application."""

import sys
import logging
import faulthandler
from logging.handlers import RotatingFileHandler
from pathlib import Path
from PySide6.QtWidgets import QApplication
from src.gui.main_window import MainWindow

LOG_DIR = Path(__file__).resolve().parent / "data" / "logs"

log = logging.getLogger("parson")


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Enable faulthandler — dumps traceback on segfault/abort to stderr AND log file
    fault_path = LOG_DIR / "crash.log"
    fault_file = open(fault_path, "a")
    faulthandler.enable(file=fault_file)

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

    # Global exception hook — catch anything that slips through
    def _global_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _global_exception

    log.info("=== Parson starting ===")

    app = QApplication(sys.argv)
    app.setApplicationName("Parson")
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    exit_code = app.exec()
    log.info(f"=== Parson exiting (code={exit_code}) ===")
    fault_file.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
