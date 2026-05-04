from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTextEdit

from .main_window import MainWindow
from .workers import DuplicateAnalysisWorker, GenericWorker, InventoryWorker


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Plex Inventory")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
