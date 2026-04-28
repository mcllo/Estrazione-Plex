from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable
from xml.etree import ElementTree as ET

import requests
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


def _token_header_name() -> str:
    return "X-" + "Plex-" + "Token"


def _resources_endpoint() -> str:
    return "https://" + "plex.tv" + "/api/resources"


def _load_resource_devices(account_token: str):
    response = requests.get(
        _resources_endpoint(),
        headers={"Accept": "application/xml", _token_header_name(): account_token.strip()},
        params={"includeHttps": "1", "includeRelay": "1", "includeIPv6": "1"},
        timeout=12,
    )
    response.raise_for_status()
    return list(ET.fromstring(response.content).findall("Device"))


def _server_devices(account_token: str):
    devices = []
    for device in _load_resource_devices(account_token):
        name = (device.get("name") or "").strip()
        provides = (device.get("provides") or "").lower()
        product = (device.get("product") or "").lower()
        if name and ("server" in provides or "plex media server" in product):
            devices.append(device)
    return devices


def _fast_server_names(account_token: str) -> list[str]:
    return sorted({(d.get("name") or "").strip() for d in _server_devices(account_token) if d.get("name")}, key=str.lower)


def _connection_uris(device) -> list[str]:
    ranked = []
    for conn in device.findall("Connection"):
        uri = (conn.get("uri") or "").strip().rstrip("/")
        if not uri:
            continue
        local = (conn.get("local") or "0") == "1"
        relay = (conn.get("relay") or "0") == "1"
        protocol = (conn.get("protocol") or "").lower()
        score = 0
        if local:
            score -= 40
        if protocol == "http":
            score -= 20
        if protocol == "https":
            score -= 5
        if relay:
            score += 50
        ranked.append((score, uri))
    return [uri for _score, uri in sorted(ranked, key=lambda item: item[0])]


def _server_token(device, account_token: str) -> str:
    value = (device.get("accessToken") or "").strip()
    return value or account_token.strip()


def _pick_connection(account_token: str, server_name: str) -> tuple[str, str]:
    wanted = server_name.strip().lower()
    matches = [d for d in _server_devices(account_token) if (d.get("name") or "").strip().lower() == wanted]
    if not matches:
        names = ", ".join(_fast_server_names(account_token))
        raise RuntimeError(f"Server non trovato: {server_name}. Disponibili: {names}")

    last_error = None
    for device in matches:
        server_token = _server_token(device, account_token)
        for uri in _connection_uris(device):
            try:
                response = requests.get(
                    uri + "/library/sections",
                    headers={_token_header_name(): server_token},
                    timeout=7,
                    verify=False,
                )
                if response.status_code == 200:
                    return uri, server_token
                last_error = RuntimeError(f"{uri}: HTTP {response.status_code}")
            except Exception as exc:
                last_error = exc
    raise RuntimeError(f"Nessuna connessione raggiungibile per {server_name}: {last_error}")


def _plex_server(base_url: str, server_token: str):
    session = requests.Session()
    session.verify = False
    try:
        return PlexServer(base_url, server_token.strip(), session=session, timeout=10)
    except TypeError:
        return PlexServer(base_url, server_token.strip(), session=session)


def _connect_main_fast(account_token: str, server_name: str):
    base_url, server_token = _pick_connection(account_token, server_name)
    return _plex_server(base_url, server_token)


def _connect_resource_fast(account_token: str, server_name: str):
    base_url, server_token = _pick_connection(account_token, server_name)
    resource = SimpleNamespace(connections=[SimpleNamespace(uri=base_url)])
    return resource, _plex_server(base_url, server_token)


def _fast_libraries(account_token: str, server_name: str):
    plex = _connect_main_fast(account_token, server_name)
    out = []
    for sec in plex.library.sections():
        sec_type = str(getattr(sec, "type", "") or "")
        if sec_type in ("movie", "show"):
            out.append({"title": sec.title, "type": sec_type})
    return out


core_mod.list_plex_servers = _fast_server_names
core_mod.list_libraries = _fast_libraries
core_mod._connect_main = _connect_main_fast
core_mod._connect_resource = _connect_resource_fast
app_mod.list_plex_servers = _fast_server_names
app_mod.list_libraries = _fast_libraries


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

    import threading

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


if __name__ == "__main__":
    raise SystemExit(app_mod.main())
