from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QMessageBox

import plex_inventory_app.app as app_mod

_original_init = app_mod.MainWindow.__init__


def _init_with_worker_refs(self):
    _original_init(self)
    self._workers = []


def _cleanup_refs(self, thread, worker):
    try:
        if thread in self._threads:
            self._threads.remove(thread)
    except Exception:
        pass
    try:
        if worker in self._workers:
            self._workers.remove(worker)
    except Exception:
        pass


def _run_background_fixed(self, fn: Callable[[], Any], on_success: Callable[[Any], None], error_title: str) -> None:
    thread = QThread(self)
    worker = app_mod.GenericWorker(fn)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(on_success)
    worker.failed.connect(lambda tb: self._background_failed(error_title, tb))
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.failed.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    self._threads.append(thread)
    self._workers.append(worker)
    thread.finished.connect(lambda: _cleanup_refs(self, thread, worker))
    thread.start()


def _start_inventory_fixed(self) -> None:
    try:
        config = self._make_config()
    except Exception as exc:
        QMessageBox.warning(self, "Configurazione", str(exc))
        return

    self.cancel_event = threading.Event()
    self.progress.setValue(0)
    self.log_box.clear()
    self._append_log("Avvio inventario...")
    self._append_log(f"Server: {config.server_name}")
    self._append_log(f"Librerie selezionate: {', '.join(config.library_names) if config.library_names else 'tutte Movies/TV'}")
    self.run_btn.setEnabled(False)
    self.cancel_btn.setEnabled(True)

    thread = QThread(self)
    worker = app_mod.InventoryWorker(config, self.cancel_event)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.progress.connect(self._on_progress)
    worker.log.connect(self._append_log)
    worker.finished.connect(self._inventory_finished)
    worker.failed.connect(self._inventory_failed)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.failed.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    self._threads.append(thread)
    self._workers.append(worker)
    thread.finished.connect(lambda: _cleanup_refs(self, thread, worker))
    thread.start()


app_mod.MainWindow.__init__ = _init_with_worker_refs
app_mod.MainWindow._run_background = _run_background_fixed
app_mod.MainWindow._start_inventory = _start_inventory_fixed

raise SystemExit(app_mod.main())
