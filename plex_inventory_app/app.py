from __future__ import annotations

import os
import sys
import threading
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .core import InventoryConfig, list_libraries, list_plex_servers, run_inventory
from .token_store import TokenStore


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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Plex Inventory - Windows Portable")
        self.resize(1080, 820)
        self.token_store = TokenStore()
        self._threads: list[QThread] = []
        self.cancel_event = threading.Event()
        self._build_ui()
        self._load_saved_token_labels()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        token_group = QGroupBox("1. Token Plex e server")
        token_layout = QGridLayout(token_group)

        self.saved_tokens = QComboBox()
        self.saved_tokens.currentTextChanged.connect(self._on_saved_token_selected)
        self.token_label = QLineEdit()
        self.token_label.setPlaceholderText("Esempio: Token casa")
        self.token_value = QLineEdit()
        self.token_value.setEchoMode(QLineEdit.Password)
        self.token_value.setPlaceholderText("X-Plex-Token")

        self.save_token_btn = QPushButton("Salva token")
        self.save_token_btn.clicked.connect(self._save_token)
        self.delete_token_btn = QPushButton("Elimina token")
        self.delete_token_btn.clicked.connect(self._delete_token)
        self.fetch_servers_btn = QPushButton("Carica server")
        self.fetch_servers_btn.clicked.connect(self._fetch_servers)

        self.server_combo = QComboBox()
        self.fetch_libraries_btn = QPushButton("Carica librerie")
        self.fetch_libraries_btn.clicked.connect(self._fetch_libraries)

        token_layout.addWidget(QLabel("Token salvati"), 0, 0)
        token_layout.addWidget(self.saved_tokens, 0, 1, 1, 2)
        token_layout.addWidget(QLabel("Etichetta"), 1, 0)
        token_layout.addWidget(self.token_label, 1, 1, 1, 2)
        token_layout.addWidget(QLabel("Token"), 2, 0)
        token_layout.addWidget(self.token_value, 2, 1, 1, 2)
        token_layout.addWidget(self.save_token_btn, 3, 0)
        token_layout.addWidget(self.delete_token_btn, 3, 1)
        token_layout.addWidget(self.fetch_servers_btn, 3, 2)
        token_layout.addWidget(QLabel("Server Plex"), 4, 0)
        token_layout.addWidget(self.server_combo, 4, 1)
        token_layout.addWidget(self.fetch_libraries_btn, 4, 2)
        layout.addWidget(token_group)

        libs_group = QGroupBox("2. Librerie da includere")
        libs_layout = QVBoxLayout(libs_group)
        self.library_list = QListWidget()
        libs_layout.addWidget(QLabel("Se non selezioni librerie, l'app include tutte le librerie Movies/TV disponibili."))
        libs_layout.addWidget(self.library_list)
        layout.addWidget(libs_group)

        options_group = QGroupBox("3. Opzioni script")
        options_layout = QGridLayout(options_group)

        self.run_preset = QComboBox()
        self.run_preset.addItems(["FAST_PRECISE", "SLOW_PRECISE"])
        self.output_profile = QComboBox()
        self.output_profile.addItems(["SLIM_BUDGET", "SLIM_RAW", "FULL"])
        self.duration_output = QComboBox()
        self.duration_output.addItems(["HMS", "BOTH"])

        self.write_csv = QCheckBox("Scrivi CSV")
        self.write_csv.setChecked(False)
        self.write_xlsx = QCheckBox("Scrivi XLSX")
        self.write_xlsx.setChecked(True)
        self.debug = QCheckBox("DEBUG: fogli Debug_XML / Debug_Streams")
        self.debug.setChecked(False)
        self.skip_short_clips = QCheckBox("Salta clip brevi TS/M2TS")
        self.skip_short_clips.setChecked(True)

        self.max_workers = QSpinBox()
        self.max_workers.setRange(1, 64)
        self.max_workers.setValue(8)
        self.http_fast = QSpinBox()
        self.http_fast.setRange(1, 16)
        self.http_fast.setValue(3)
        self.http_slow = QSpinBox()
        self.http_slow.setRange(1, 16)
        self.http_slow.setValue(1)
        self.clip_min_seconds = QSpinBox()
        self.clip_min_seconds.setRange(1, 3600)
        self.clip_min_seconds.setValue(300)
        self.top_n_movies = QSpinBox()
        self.top_n_movies.setRange(0, 999999)
        self.top_n_movies.setValue(0)
        self.top_n_shows = QSpinBox()
        self.top_n_shows.setRange(0, 999999)
        self.top_n_shows.setValue(0)

        self.output_basename = QLineEdit("plex_inventory_fast_slim")
        self.output_dir = QLineEdit(str(Path.home() / "Downloads"))
        self.browse_btn = QPushButton("Scegli cartella...")
        self.browse_btn.clicked.connect(self._browse_output_dir)

        options_layout.addWidget(QLabel("RUN_PRESET"), 0, 0)
        options_layout.addWidget(self.run_preset, 0, 1)
        options_layout.addWidget(QLabel("OUTPUT_PROFILE"), 0, 2)
        options_layout.addWidget(self.output_profile, 0, 3)
        options_layout.addWidget(QLabel("DURATION_OUTPUT"), 0, 4)
        options_layout.addWidget(self.duration_output, 0, 5)

        options_layout.addWidget(self.write_csv, 1, 0)
        options_layout.addWidget(self.write_xlsx, 1, 1)
        options_layout.addWidget(self.debug, 1, 2, 1, 2)
        options_layout.addWidget(self.skip_short_clips, 1, 4)
        options_layout.addWidget(self.clip_min_seconds, 1, 5)

        options_layout.addWidget(QLabel("MAX_WORKERS"), 2, 0)
        options_layout.addWidget(self.max_workers, 2, 1)
        options_layout.addWidget(QLabel("HTTP FAST"), 2, 2)
        options_layout.addWidget(self.http_fast, 2, 3)
        options_layout.addWidget(QLabel("HTTP SLOW"), 2, 4)
        options_layout.addWidget(self.http_slow, 2, 5)

        options_layout.addWidget(QLabel("TOP_N_MOVIES (0=tutti)"), 3, 0)
        options_layout.addWidget(self.top_n_movies, 3, 1)
        options_layout.addWidget(QLabel("TOP_N_SHOWS (0=tutti)"), 3, 2)
        options_layout.addWidget(self.top_n_shows, 3, 3)

        options_layout.addWidget(QLabel("Nome file base"), 4, 0)
        options_layout.addWidget(self.output_basename, 4, 1, 1, 2)
        options_layout.addWidget(QLabel("Cartella output"), 5, 0)
        options_layout.addWidget(self.output_dir, 5, 1, 1, 4)
        options_layout.addWidget(self.browse_btn, 5, 5)
        layout.addWidget(options_group)

        run_group = QGroupBox("4. Esecuzione")
        run_layout = QVBoxLayout(run_group)
        buttons = QHBoxLayout()
        self.run_btn = QPushButton("Avvia inventario")
        self.run_btn.clicked.connect(self._start_inventory)
        self.cancel_btn = QPushButton("Interrompi")
        self.cancel_btn.clicked.connect(self._cancel_inventory)
        self.cancel_btn.setEnabled(False)
        buttons.addWidget(self.run_btn)
        buttons.addWidget(self.cancel_btn)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status_label = QLabel("Pronto")
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setLineWrapMode(QTextEdit.NoWrap)
        run_layout.addLayout(buttons)
        run_layout.addWidget(self.progress)
        run_layout.addWidget(self.status_label)
        run_layout.addWidget(self.log_box, stretch=1)
        layout.addWidget(run_group, stretch=1)

    def _load_saved_token_labels(self) -> None:
        current = self.saved_tokens.currentText()
        self.saved_tokens.blockSignals(True)
        self.saved_tokens.clear()
        self.saved_tokens.addItem("")
        for label in self.token_store.labels():
            self.saved_tokens.addItem(label)
        idx = self.saved_tokens.findText(current)
        if idx >= 0:
            self.saved_tokens.setCurrentIndex(idx)
        self.saved_tokens.blockSignals(False)

    def _on_saved_token_selected(self, label: str) -> None:
        if not label.strip():
            return
        try:
            token = self.token_store.load(label)
            self.token_label.setText(label)
            self.token_value.setText(token)
            self._append_log(f"Token caricato: {label}")
        except Exception as exc:
            QMessageBox.warning(self, "Token", f"Impossibile caricare il token: {exc}")

    def _save_token(self) -> None:
        try:
            self.token_store.save(self.token_label.text(), self.token_value.text())
            self._append_log(f"Token salvato: {self.token_label.text().strip()}")
            self._load_saved_token_labels()
        except Exception as exc:
            QMessageBox.warning(self, "Token", str(exc))

    def _delete_token(self) -> None:
        label = self.token_label.text().strip() or self.saved_tokens.currentText().strip()
        if not label:
            return
        self.token_store.delete(label)
        self.token_label.clear()
        self.token_value.clear()
        self._append_log(f"Token eliminato: {label}")
        self._load_saved_token_labels()

    def _selected_token(self) -> str:
        token = self.token_value.text().strip()
        if not token:
            label = self.saved_tokens.currentText().strip()
            if label:
                token = self.token_store.load(label)
        return token

    def _fetch_servers(self) -> None:
        token = self._selected_token()
        if not token:
            QMessageBox.warning(self, "Server", "Inserisci o seleziona un token Plex.")
            return
        self._append_log("Carico server Plex...")
        self._run_background(lambda: list_plex_servers(token), self._servers_loaded, "Caricamento server fallito")

    def _servers_loaded(self, servers: list[str]) -> None:
        self.server_combo.clear()
        self.server_combo.addItems(servers)
        if not servers:
            QMessageBox.information(self, "Server", "Nessun server Plex trovato per questo token.")
        elif len(servers) == 1:
            self.server_combo.setCurrentIndex(0)
        self._append_log(f"Server trovati: {len(servers)}")

    def _fetch_libraries(self) -> None:
        token = self._selected_token()
        server = self.server_combo.currentText().strip()
        if not token or not server:
            QMessageBox.warning(self, "Librerie", "Inserisci token e seleziona un server Plex.")
            return
        self._append_log(f"Carico librerie da {server}...")
        self._run_background(lambda: list_libraries(token, server), self._libraries_loaded, "Caricamento librerie fallito")

    def _libraries_loaded(self, libs: list[dict[str, str]]) -> None:
        self.library_list.clear()
        for lib in libs:
            title = lib.get("title", "")
            lib_type = lib.get("type", "")
            item = QListWidgetItem(f"{title} ({lib_type})")
            item.setData(Qt.UserRole, title)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.library_list.addItem(item)
        self._append_log(f"Librerie Movies/TV trovate: {len(libs)}")

    def _browse_output_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Scegli cartella output", self.output_dir.text())
        if chosen:
            self.output_dir.setText(chosen)

    def _selected_libraries(self) -> list[str]:
        libs = []
        for i in range(self.library_list.count()):
            item = self.library_list.item(i)
            if item.checkState() == Qt.Checked:
                libs.append(str(item.data(Qt.UserRole)))
        return libs

    def _make_config(self) -> InventoryConfig:
        token = self._selected_token()
        server = self.server_combo.currentText().strip()
        if not token:
            raise ValueError("Inserisci o seleziona un token Plex.")
        if not server:
            raise ValueError("Carica e seleziona un server Plex.")
        if not self.write_csv.isChecked() and not self.write_xlsx.isChecked():
            raise ValueError("Seleziona almeno CSV o XLSX.")
        output_dir = self.output_dir.text().strip()
        if not output_dir:
            raise ValueError("Scegli una cartella output.")
        return InventoryConfig(
            token=token,
            server_name=server,
            library_names=self._selected_libraries(),
            output_dir=output_dir,
            output_basename=self.output_basename.text().strip() or "plex_inventory_fast_slim",
            run_preset=self.run_preset.currentText(),
            max_workers=self.max_workers.value(),
            http_concurrency_fast=self.http_fast.value(),
            http_concurrency_slow=self.http_slow.value(),
            write_csv=self.write_csv.isChecked(),
            write_xlsx=self.write_xlsx.isChecked(),
            duration_output=self.duration_output.currentText(),
            output_profile=self.output_profile.currentText(),
            debug=self.debug.isChecked(),
            top_n_movies=self.top_n_movies.value() or None,
            top_n_shows=self.top_n_shows.value() or None,
            skip_short_clips=self.skip_short_clips.isChecked(),
            clip_min_seconds=self.clip_min_seconds.value(),
        )

    def _start_inventory(self) -> None:
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
        worker = InventoryWorker(config, self.cancel_event)
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
        thread.finished.connect(lambda: self._threads.remove(thread) if thread in self._threads else None)
        thread.start()

    def _cancel_inventory(self) -> None:
        self.cancel_event.set()
        self.cancel_btn.setEnabled(False)
        self._append_log("Richiesta interruzione inviata. Le parti già in esecuzione possono finire prima del salvataggio.")

    @Slot(int, int, str)
    def _on_progress(self, done: int, total: int, msg: str) -> None:
        if total <= 0:
            self.progress.setValue(0)
        else:
            self.progress.setValue(int(done / max(total, 1) * 100))
        self.status_label.setText(msg)

    @Slot(object)
    def _inventory_finished(self, result: Any) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress.setValue(100)
        self.status_label.setText("Completato")
        paths = []
        if getattr(result, "csv_path", None):
            paths.append(result.csv_path)
        if getattr(result, "xlsx_path", None):
            paths.append(result.xlsx_path)
        self._append_log("Completato.")
        QMessageBox.information(self, "Inventario completato", "File creati:\n" + "\n".join(paths) if paths else "Inventario completato.")

    @Slot(str)
    def _inventory_failed(self, tb: str) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Errore")
        self._append_log(tb)
        QMessageBox.critical(self, "Errore", "Inventario fallito. Vedi log.")

    def _run_background(self, fn: Callable[[], Any], on_success: Callable[[Any], None], error_title: str) -> None:
        thread = QThread(self)
        worker = GenericWorker(fn)
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
        thread.finished.connect(lambda: self._threads.remove(thread) if thread in self._threads else None)
        thread.start()

    def _background_failed(self, title: str, tb: str) -> None:
        self._append_log(tb)
        QMessageBox.critical(self, title, tb.splitlines()[-1] if tb.splitlines() else tb)

    @Slot(str)
    def _append_log(self, text: str) -> None:
        self.log_box.append(text)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Plex Inventory")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
