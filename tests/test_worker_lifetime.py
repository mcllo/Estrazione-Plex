from pathlib import Path

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest

try:
    from PySide6.QtWidgets import QApplication
except Exception as exc:  # pragma: no cover - env-dependent
    pytest.skip(f"PySide6 non disponibile nel runner: {exc}", allow_module_level=True)

from plex_inventory_app.app import MainWindow


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_mainwindow_workers_type_coherent():
    _app()
    win = MainWindow()
    assert isinstance(win._workers, list)


def test_run_duplicate_analysis_tracks_worker(tmp_path: Path, monkeypatch):
    _app()
    library = pd.DataFrame([
        {"type":"movie","title_or_series":"A","season":"","episode":"","episode_title":"","year":2020,"resolution":"1080p","hdr":"SDR","videoCodec":"h264","container":"mkv","duration_hms":"01:00:00","bitrate_mbps_video":5.0,"audio_it_bitrate_mbps":0.6,"audio_it_quality":"DD 5.1","audio_en_bitrate_mbps":0.6,"audio_en_quality":"DD 5.1","size_gib":4.2,"imdb_id":"tt1","rating_key":"1","file":"/a.mkv"}
    ])
    xlsx = tmp_path / "inventory.xlsx"
    library.to_excel(xlsx, sheet_name="Library", index=False)

    win = MainWindow()
    win.dup_inventory_path.setText(str(xlsx))
    win.dup_output_dir.setText(str(tmp_path))

    monkeypatch.setattr("plex_inventory_app.app.QThread.start", lambda self: None)

    before = len(win._workers)
    win._run_duplicate_analysis()
    assert len(win._workers) == before + 1
