from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QSpinBox, QVBoxLayout, QWidget


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
