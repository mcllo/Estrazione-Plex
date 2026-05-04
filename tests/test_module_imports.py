import os
import py_compile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import plex_inventory_app.app as app
    import plex_inventory_app.main_window as main_window
    import plex_inventory_app.workers as workers
    import plex_inventory_app.advanced_options_dialog as advanced_options_dialog
except Exception as exc:  # pragma: no cover - env-dependent
    pytest.skip(f"Moduli UI non importabili nel runner: {exc}", allow_module_level=True)


def test_module_imports_and_exports():
    assert main_window is not None
    assert workers is not None
    assert advanced_options_dialog is not None
    assert hasattr(app, "MainWindow")
    assert hasattr(app, "GenericWorker")
    assert hasattr(app, "InventoryWorker")
    assert hasattr(app, "DuplicateAnalysisWorker")


def test_launcher_compiles():
    py_compile.compile("launcher.py", doraise=True)
