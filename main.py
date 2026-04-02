"""Entry point for the Parson torrent parser application."""

import sys
import logging
from PySide6.QtWidgets import QApplication
from src.gui.main_window import MainWindow


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Parson")
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
