from __future__ import annotations

import threading
from typing import Any, Callable

import requests
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QMessageBox

import plex_inventory_app.app as app_mod
import plex_inventory_app.core as core_mod

_original_init = app_mod.MainWindow.__init__

try:
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
except Exception:
    pass


def _safe_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _redact(text: str, token: str) -> str:
    if not token:
        return text
    return str(text).replace(token, "***TOKEN***")


def _connection_uri(conn: Any) -> str:
    return str(getattr(conn, "uri", "") or "").rstrip("/")


def _connection_score(conn: Any) -> tuple[int, str]:
    # Colab runs outside the LAN, so it normally uses a remote/relay route.
    # On Windows a local plex.direct route can be chosen first and fail.
    # Prefer remote non-relay, then relay, then local as last resort.
    uri = _connection_uri(conn)
    local = _safe_bool(getattr(conn, "local", False))
    relay = _safe_bool(getattr(conn, "relay", False))
    protocol = str(getattr(conn, "protocol", "") or "").lower()
    score = 0
    if local:
        score += 100
    if relay:
        score += 30
    if protocol == "https":
        score -= 5
    if protocol == "http":
        score -= 2
    return score, uri


def _token_candidates(resource: Any, account_token: str) -> list[str]:
    tokens = []
    for attr in ("accessToken", "token"):
        value = str(getattr(resource, attr, "") or "").strip()
        if value and value not in tokens:
            tokens.append(value)
    if account_token.strip() not in tokens:
        tokens.append(account_token.strip())
    return tokens


def _make_plex(base_url: str, token: str) -> PlexServer:
    session = requests.Session()
    session.verify = False
    try:
        return PlexServer(base_url, token, timeout=8, session=session)
    except TypeError:
        return PlexServer(base_url, token, session=session)


def _connect_resource_colab_plus(token: str, server_name: str):
    account_token = token.strip()
    account = MyPlexAccount(token=account_token)
    resource = account.resource(server_name.strip())

    attempts: list[str] = []

    # 1) Same first attempt as the working Colab script.
    try:
        return resource, resource.connect(timeout=6)
    except Exception as exc:
        attempts.append("resource.connect: " + _redact(repr(exc), account_token))

    # 2) Fallback inspired by the script's BASEURL reuse, but try every Plex URL.
    conns = list(getattr(resource, "connections", None) or [])
    conns = [c for c in conns if _connection_uri(c)]
    conns = sorted(conns, key=_connection_score)
    tokens = _token_candidates(resource, account_token)

    for conn in conns:
        uri = _connection_uri(conn)
        local = _safe_bool(getattr(conn, "local", False))
        relay = _safe_bool(getattr(conn, "relay", False))
        for candidate in tokens:
            try:
                plex = _make_plex(uri, candidate)
                # Force a tiny authenticated call so we only accept a usable server.
                plex.library.sections()
                attempts.append(f"OK: {uri} local={local} relay={relay}")
                return resource, plex
            except Exception as exc:
                msg = _redact(repr(exc), candidate)
                attempts.append(f"FAIL: {uri} local={local} relay={relay}: {msg}")

    preview = "\n".join(attempts[-12:])
    raise RuntimeError(
        "Impossibile connettersi al server Plex. Tentativi effettuati:\n" + preview
    )


def _connect_main_colab_plus(token: str, server_name: str):
    _resource, plex = _connect_resource_colab_plus(token, server_name)
    return plex


core_mod._connect_resource = _connect_resource_colab_plus
core_mod._connect_main = _connect_main_colab_plus


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
