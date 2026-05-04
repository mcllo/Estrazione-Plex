from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .core import InventoryConfig, list_libraries, list_plex_servers, run_inventory
from .token_store import TokenStore
from .duplicate_analysis import analyze_duplicates
from .duplicate_policy_v12 import POLICY_VERSION


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
            out = analyze_duplicates(Path(self.inventory_path), Path(self.output_dir), log_callback=lambda m: self.log.emit(m))
            self.finished.emit(out)
        except Exception:
            self.failed.emit(traceback.format_exc())


class AdvancedOptionsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Impostazioni avanzate")
        self.resize(620, 420)

        root_layout = QVBoxLayout(self)
        form = QFormLayout()

        self.duration_output = QComboBox()
        self.duration_output.addItems(["HMS", "BOTH"])
        self.debug = QCheckBox("DEBUG: fogli Debug_XML / Debug_Streams")
        self.skip_short_clips = QCheckBox("Salta clip brevi TS/M2TS")

        self.clip_min_seconds = QSpinBox()
        self.clip_min_seconds.setRange(1, 3600)
        self.max_workers = QSpinBox()
        self.max_workers.setRange(1, 64)
        self.http_fast = QSpinBox()
        self.http_fast.setRange(1, 16)
        self.http_slow = QSpinBox()
        self.http_slow.setRange(1, 16)
        self.top_n_movies = QSpinBox()
        self.top_n_movies.setRange(0, 999999)
        self.top_n_movies.setToolTip("0 = tutti i film")
        self.top_n_shows = QSpinBox()
        self.top_n_shows.setRange(0, 999999)
        self.top_n_shows.setToolTip("0 = tutte le serie")

        form.addRow("DURATION_OUTPUT", self.duration_output)
        form.addRow(self.debug)
        form.addRow(self.skip_short_clips)
        form.addRow("CLIP_MIN_SECONDS", self.clip_min_seconds)
        form.addRow("MAX_WORKERS", self.max_workers)
        form.addRow("HTTP FAST", self.http_fast)
        form.addRow("HTTP SLOW", self.http_slow)
        form.addRow("TOP_N_MOVIES (0=tutti)", self.top_n_movies)
        form.addRow("TOP_N_SHOWS (0=tutti)", self.top_n_shows)
        root_layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root_layout.addWidget(buttons)

    def set_values(
        self,
        *,
        duration_output: str,
        debug: bool,
        skip_short_clips: bool,
        clip_min_seconds: int,
        max_workers: int,
        http_fast: int,
        http_slow: int,
        top_n_movies: int,
        top_n_shows: int,
    ) -> None:
        self.duration_output.setCurrentText(duration_output)
        self.debug.setChecked(debug)
        self.skip_short_clips.setChecked(skip_short_clips)
        self.clip_min_seconds.setValue(clip_min_seconds)
        self.max_workers.setValue(max_workers)
        self.http_fast.setValue(http_fast)
        self.http_slow.setValue(http_slow)
        self.top_n_movies.setValue(top_n_movies)
        self.top_n_shows.setValue(top_n_shows)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Plex Inventory - Windows Portable")
        self.resize(1080, 820)
        self.token_store = TokenStore()
        self._threads: list[QThread] = []
        self._workers: list[QObject] = []
        self.cancel_event = threading.Event()
        self.inventory_started_at: float | None = None
        self._advanced_duration_output = "HMS"
        self._advanced_debug = False
        self._advanced_skip_short_clips = True
        self._advanced_clip_min_seconds = 300
        self._advanced_max_workers = 8
        self._advanced_http_fast = 3
        self._advanced_http_slow = 1
        self._advanced_top_n_movies = 0
        self._advanced_top_n_shows = 0
        self.last_inventory_report_path: str | None = None
        self._build_ui()
        self._load_saved_token_labels()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        inventory_tab = QWidget()
        tabs.addTab(inventory_tab, "Inventario Plex")
        inventory_layout = QVBoxLayout(inventory_tab)

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
        inventory_layout.addWidget(token_group)

        libs_group = QGroupBox("2. Librerie da includere")
        libs_layout = QVBoxLayout(libs_group)
        self.library_list = QListWidget()
        self.library_list.setMinimumHeight(230)
        libs_layout.addWidget(QLabel("Se non selezioni librerie, l'app include tutte le librerie Movies/TV disponibili."))
        libs_layout.addWidget(self.library_list)

        options_group = QGroupBox("3. Opzioni script")
        options_layout = QVBoxLayout(options_group)

        self.run_preset = QComboBox()
        self.run_preset.addItems(["FAST_PRECISE", "SLOW_PRECISE"])
        self.output_profile = QComboBox()
        self.output_profile.addItems(["SLIM_BUDGET", "SLIM_RAW", "FULL"])
        self.write_csv = QCheckBox("Scrivi CSV")
        self.write_csv.setChecked(False)
        self.write_xlsx = QCheckBox("Scrivi XLSX")
        self.write_xlsx.setChecked(True)

        self.output_basename = QLineEdit("plex_inventory_fast_slim")
        self.output_dir = QLineEdit(str(Path.home() / "Downloads"))
        self.browse_btn = QPushButton("Scegli cartella...")
        self.browse_btn.clicked.connect(self._browse_output_dir)
        self.advanced_btn = QPushButton("Impostazioni avanzate...")
        self.advanced_btn.clicked.connect(self._open_advanced_options)

        main_options_layout = QFormLayout()
        main_options_layout.addRow("Modalità elaborazione", self.run_preset)
        main_options_layout.addRow("Profilo output", self.output_profile)
        format_row = QHBoxLayout()
        format_row.addWidget(self.write_xlsx)
        format_row.addWidget(self.write_csv)
        format_row.addStretch(1)
        main_options_layout.addRow("Formato output", format_row)
        main_options_layout.addRow("Nome file base", self.output_basename)
        output_dir_row = QHBoxLayout()
        output_dir_row.addWidget(self.output_dir, 1)
        output_dir_row.addWidget(self.browse_btn)
        main_options_layout.addRow("Cartella output", output_dir_row)
        main_options_layout.addRow("", self.advanced_btn)
        options_layout.addLayout(main_options_layout)
        options_layout.addStretch(1)

        middle_row = QHBoxLayout()
        middle_row.addWidget(libs_group, 2)
        middle_row.addWidget(options_group, 3)
        inventory_layout.addLayout(middle_row, 1)

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
        self.eta_label = QLabel("Tempo: 00:00:00 | ETA residua: calcolo...")
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setLineWrapMode(QTextEdit.NoWrap)
        self.log_box.setMinimumHeight(280)
        run_layout.addLayout(buttons)
        run_layout.addWidget(self.progress)
        run_layout.addWidget(self.status_label)
        run_layout.addWidget(self.eta_label)
        run_layout.addWidget(self.log_box, stretch=1)
        inventory_layout.addWidget(run_group, stretch=3)

        dup_tab = QWidget()
        tabs.addTab(dup_tab, "Analisi duplicati")
        dup_layout = QVBoxLayout(dup_tab)
        dup_form = QFormLayout()
        self.dup_inventory_path = QLineEdit()
        self.dup_pick_inventory_btn = QPushButton("Scegli report...")
        self.dup_pick_inventory_btn.clicked.connect(self._browse_duplicate_inventory)
        inv_row = QHBoxLayout()
        inv_row.addWidget(self.dup_inventory_path, 1)
        inv_row.addWidget(self.dup_pick_inventory_btn)
        dup_form.addRow("Report inventario", inv_row)
        self.dup_output_dir = QLineEdit(str(Path.home() / "Downloads"))
        self.dup_pick_output_btn = QPushButton("Scegli cartella...")
        self.dup_pick_output_btn.clicked.connect(self._browse_duplicate_output)
        out_row = QHBoxLayout()
        out_row.addWidget(self.dup_output_dir, 1)
        out_row.addWidget(self.dup_pick_output_btn)
        dup_form.addRow("Output", out_row)
        dup_form.addRow("Policy", QLabel(f"Regole integrate: {POLICY_VERSION}"))
        dup_layout.addLayout(dup_form)
        self.dup_run_btn = QPushButton("Genera report duplicati")
        self.dup_run_btn.clicked.connect(self._run_duplicate_analysis)
        dup_layout.addWidget(self.dup_run_btn)
        self.dup_progress = QProgressBar()
        self.dup_progress.setRange(0, 100)
        self.dup_progress.setValue(0)
        dup_layout.addWidget(self.dup_progress)
        self.dup_log_box = QTextEdit()
        self.dup_log_box.setReadOnly(True)
        dup_layout.addWidget(self.dup_log_box, stretch=1)

    def _browse_duplicate_inventory(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Scegli report inventario XLSX", self.dup_inventory_path.text(), "Excel (*.xlsx)")
        if chosen:
            self.dup_inventory_path.setText(chosen)
            if not self.dup_output_dir.text().strip():
                self.dup_output_dir.setText(str(Path(chosen).parent))

    def _browse_duplicate_output(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Scegli cartella output", self.dup_output_dir.text())
        if chosen:
            self.dup_output_dir.setText(chosen)

    def _dup_log(self, text: str) -> None:
        self.dup_log_box.append(text)
        self.dup_log_box.verticalScrollBar().setValue(self.dup_log_box.verticalScrollBar().maximum())

    def _track_worker(self, thread: QThread, worker: QObject) -> None:
        self._threads.append(thread)
        self._workers.append(worker)
        thread.finished.connect(lambda: self._threads.remove(thread) if thread in self._threads else None)
        thread.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)

    def _run_duplicate_analysis(self) -> None:
        inventory = self.dup_inventory_path.text().strip()
        output_dir = self.dup_output_dir.text().strip()
        if not inventory:
            QMessageBox.warning(self, "Analisi duplicati", "Seleziona un report inventario XLSX oppure genera prima un inventario dal tab Inventario Plex.")
            return
        if not inventory.lower().endswith(".xlsx"):
            QMessageBox.warning(self, "Analisi duplicati", "L'analisi duplicati richiede un file XLSX.")
            return
        if not output_dir:
            output_dir = str(Path(inventory).parent)
            self.dup_output_dir.setText(output_dir)
        self.dup_log_box.clear()
        self._dup_log("Avvio analisi duplicati...")
        self._dup_log(f"Report: {inventory}")
        self._dup_log(f"Output: {output_dir}")
        self._dup_log("Lettura workbook XLSX...")
        self.dup_run_btn.setEnabled(False)
        self.dup_run_btn.setText("Analisi in corso...")
        self.dup_pick_inventory_btn.setEnabled(False)
        self.dup_pick_output_btn.setEnabled(False)
        self.dup_progress.setRange(0, 0)
        self._dup_log("Creo worker analisi duplicati...")
        thread = QThread(self)
        worker = DuplicateAnalysisWorker(inventory, output_dir)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._dup_log)
        worker.finished.connect(self._duplicate_finished)
        worker.failed.connect(self._duplicate_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._track_worker(thread, worker)
        self._dup_log("Avvio thread analisi duplicati...")
        thread.start()

    @Slot(object)
    def _duplicate_finished(self, out_path: Any) -> None:
        self.dup_run_btn.setEnabled(True)
        self.dup_run_btn.setText("Genera report duplicati")
        self.dup_pick_inventory_btn.setEnabled(True)
        self.dup_pick_output_btn.setEnabled(True)
        self.dup_progress.setRange(0, 100)
        self.dup_progress.setValue(100)
        QMessageBox.information(self, "Analisi duplicati completata", f"File generato:\n{out_path}")

    @Slot(str)
    def _duplicate_failed(self, tb: str) -> None:
        self.dup_run_btn.setEnabled(True)
        self.dup_run_btn.setText("Genera report duplicati")
        self.dup_pick_inventory_btn.setEnabled(True)
        self.dup_pick_output_btn.setEnabled(True)
        self.dup_progress.setRange(0, 100)
        self.dup_progress.setValue(0)
        self._dup_log(tb)
        QMessageBox.critical(self, "Analisi duplicati", tb.splitlines()[-1] if tb.splitlines() else tb)

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
        self._run_background(
            lambda: list_plex_servers(token),
            self._servers_loaded,
            "Caricamento server fallito",
            on_error=self._servers_failed,
        )

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
        self._run_background(
            lambda: list_libraries(token, server),
            self._libraries_loaded,
            "Caricamento librerie fallito",
            on_error=self._libraries_failed,
        )

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

    def _open_advanced_options(self) -> None:
        dialog = AdvancedOptionsDialog(self)
        dialog.set_values(
            duration_output=self._advanced_duration_output,
            debug=self._advanced_debug,
            skip_short_clips=self._advanced_skip_short_clips,
            clip_min_seconds=self._advanced_clip_min_seconds,
            max_workers=self._advanced_max_workers,
            http_fast=self._advanced_http_fast,
            http_slow=self._advanced_http_slow,
            top_n_movies=self._advanced_top_n_movies,
            top_n_shows=self._advanced_top_n_shows,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self._advanced_duration_output = dialog.duration_output.currentText()
        self._advanced_debug = dialog.debug.isChecked()
        self._advanced_skip_short_clips = dialog.skip_short_clips.isChecked()
        self._advanced_clip_min_seconds = dialog.clip_min_seconds.value()
        self._advanced_max_workers = dialog.max_workers.value()
        self._advanced_http_fast = dialog.http_fast.value()
        self._advanced_http_slow = dialog.http_slow.value()
        self._advanced_top_n_movies = dialog.top_n_movies.value()
        self._advanced_top_n_shows = dialog.top_n_shows.value()

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
            max_workers=self._advanced_max_workers,
            http_concurrency_fast=self._advanced_http_fast,
            http_concurrency_slow=self._advanced_http_slow,
            write_csv=self.write_csv.isChecked(),
            write_xlsx=self.write_xlsx.isChecked(),
            duration_output=self._advanced_duration_output,
            output_profile=self.output_profile.currentText(),
            debug=self._advanced_debug,
            top_n_movies=self._advanced_top_n_movies or None,
            top_n_shows=self._advanced_top_n_shows or None,
            skip_short_clips=self._advanced_skip_short_clips,
            clip_min_seconds=self._advanced_clip_min_seconds,
        )

    def _start_inventory(self) -> None:
        try:
            config = self._make_config()
        except Exception as exc:
            QMessageBox.warning(self, "Configurazione", str(exc))
            return
        self.cancel_event = threading.Event()
        self.progress.setValue(0)
        self.inventory_started_at = time.monotonic()
        self.eta_label.setText("Tempo: 00:00:00 | ETA residua: calcolo...")
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
        self._track_worker(thread, worker)
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
        elapsed = self._elapsed_seconds()
        if done <= 0 or total <= 0:
            self.eta_label.setText(f"Tempo: {self._fmt_duration(elapsed)} | ETA residua: calcolo...")
            return
        avg_seconds_per_job = elapsed / max(done, 1)
        eta_remaining = max(0.0, (total - done) * avg_seconds_per_job)
        self.eta_label.setText(
            f"Tempo: {self._fmt_duration(elapsed)} | ETA residua: {self._fmt_duration(eta_remaining)}"
        )

    @Slot(object)
    def _inventory_finished(self, result: Any) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress.setValue(100)
        self.status_label.setText("Completato")
        self.eta_label.setText(f"Completato in {self._fmt_duration(getattr(result, 'elapsed_seconds', self._elapsed_seconds()))}")
        self.inventory_started_at = None
        paths = []
        if getattr(result, "csv_path", None):
            paths.append(result.csv_path)
        if getattr(result, "xlsx_path", None):
            paths.append(result.xlsx_path)
            self.last_inventory_report_path = result.xlsx_path
            self.dup_inventory_path.setText(result.xlsx_path)
            self.dup_output_dir.setText(str(Path(result.xlsx_path).parent))
        elif getattr(result, "csv_path", None):
            self._dup_log("Inventario completato solo in CSV: l'analisi duplicati richiede un XLSX")
        self._append_log("Completato.")
        QMessageBox.information(self, "Inventario completato", "File creati:\n" + "\n".join(paths) if paths else "Inventario completato.")

    @Slot(str)
    def _inventory_failed(self, tb: str) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Errore")
        self.eta_label.setText(f"Tempo: {self._fmt_duration(self._elapsed_seconds())} | ETA residua: --")
        self.inventory_started_at = None
        self._append_log(tb)
        QMessageBox.critical(self, "Errore", "Inventario fallito. Vedi log.")

    def _run_background(
        self,
        fn: Callable[[], Any],
        on_success: Callable[[Any], None],
        error_title: str,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        thread = QThread(self)
        worker = GenericWorker(fn)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(on_success)
        worker.failed.connect(lambda tb: self._background_failed(error_title, tb, on_error=on_error))
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._track_worker(thread, worker)
        thread.start()

    def _background_failed(self, title: str, tb: str, on_error: Callable[[str], None] | None = None) -> None:
        self._append_log(tb)
        if on_error is not None:
            on_error(tb)
            return
        QMessageBox.critical(self, title, tb.splitlines()[-1] if tb.splitlines() else tb)

    def _servers_failed(self, tb: str) -> None:
        self.server_combo.clear()
        self._append_log(tb)
        self._append_log("Timeout caricamento server" if "timeout" in tb.lower() else "Errore caricamento server")
        message = tb.splitlines()[-1] if tb.splitlines() else tb
        if "timeout" in tb.lower():
            message = "Timeout nel caricamento server Plex. Verifica connessione, token o stato di plex.tv e riprova."
        QMessageBox.warning(self, "Server", f"Impossibile caricare i server Plex.\n{message}")

    def _libraries_failed(self, tb: str) -> None:
        self.library_list.clear()
        message = tb.splitlines()[-1] if tb.splitlines() else tb
        QMessageBox.warning(self, "Librerie", f"Impossibile caricare le librerie.\n{message}")

    @Slot(str)
    def _append_log(self, text: str) -> None:
        self.log_box.append(text)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def _elapsed_seconds(self) -> float:
        if self.inventory_started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self.inventory_started_at)

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        total_seconds = max(0, int(seconds))
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Plex Inventory")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
