from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal, Slot

from .core import InventoryConfig, run_inventory
from .duplicate_analysis import analyze_duplicates


class GenericWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(self.fn())
        except Exception:
            self.failed.emit(traceback.format_exc())


class InventoryWorker(QObject):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, config: InventoryConfig, cancel_event: threading.Event) -> None:
        super().__init__()
        self.config = config
        self.cancel_event = cancel_event

    @Slot()
    def run(self) -> None:
        try:
            result = run_inventory(
                self.config,
                progress_callback=lambda done, total, msg: self.progress.emit(done, total, msg),
                log_callback=lambda msg: self.log.emit(msg),
                cancel_event=self.cancel_event,
            )
            self.finished.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())


class DuplicateAnalysisWorker(QObject):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, inventory_path: str, output_dir: str) -> None:
        super().__init__()
        self.inventory_path = inventory_path
        self.output_dir = output_dir

    @Slot()
    def run(self) -> None:
        try:
            self.log.emit("Thread analisi duplicati avviato")
            self.log.emit("Chiamo analyze_duplicates...")
            out = analyze_duplicates(
                Path(self.inventory_path),
                Path(self.output_dir),
                log_callback=lambda m: self.log.emit(m),
                progress_callback=lambda done, total, msg: self.progress.emit(done, total, msg),
            )
            self.finished.emit(out)
        except Exception:
            self.failed.emit(traceback.format_exc())
