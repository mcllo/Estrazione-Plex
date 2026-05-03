from __future__ import annotations

import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

import requests
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QMessageBox

import plex_inventory_app.app as app_mod
import plex_inventory_app.core as core_mod
from plex_inventory_app.debug_wide_patch import apply as apply_debug_wide_patch

_original_init = app_mod.MainWindow.__init__

# Timeout/retry più adatti a Plex Media Server su Nvidia Shield + dischi USB.
# Evitano falsi timeout quando la Shield, Plex o i dischi esterni rispondono lentamente.
RESOURCE_CONNECT_TIMEOUT_S = 12
FALLBACK_CONNECT_TIMEOUT_S = 12
THREAD_CONNECT_TIMEOUT_S = 12
XML_RETRY_ATTEMPTS = 4
XML_RETRY_BACKOFF_S = 0.75

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
    uri = _connection_uri(conn)
    local = _safe_bool(getattr(conn, "local", False))
    relay = _safe_bool(getattr(conn, "relay", False))
    protocol = str(getattr(conn, "protocol", "") or "").lower()
    score = 0
    # On this Windows network FortiGuard blocks plex.direct remote/relay URLs.
    # Try LAN routes before remote routes.
    if local:
        score -= 100
    if relay:
        score += 50
    if protocol == "http":
        score -= 5
    if protocol == "https":
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


def _local_ip_from_plex_direct(uri: str) -> str | None:
    try:
        parsed = urlparse(uri)
        host = parsed.hostname or ""
        first = host.split(".", 1)[0]
        parts = first.split("-")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return ".".join(parts)
    except Exception:
        return None
    return None


def _candidate_uris(conn: Any) -> list[str]:
    uri = _connection_uri(conn)
    local = _safe_bool(getattr(conn, "local", False))
    out: list[str] = []
    if local:
        ip = _local_ip_from_plex_direct(uri)
        port = urlparse(uri).port or 32400
        if ip:
            # Plex on LAN normally accepts this even when plex.direct certificate/DNS is problematic.
            out.append(f"http://{ip}:{port}")
            out.append(f"https://{ip}:{port}")
    if uri:
        out.append(uri)
    deduped: list[str] = []
    for item in out:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def _make_plex(base_url: str, token: str) -> PlexServer:
    session = requests.Session()
    session.verify = False
    try:
        return PlexServer(base_url, token, timeout=FALLBACK_CONNECT_TIMEOUT_S, session=session)
    except TypeError:
        return PlexServer(base_url, token, session=session)


def _connect_resource_colab_plus(token: str, server_name: str):
    account_token = token.strip()
    account = MyPlexAccount(token=account_token)
    resource = account.resource(server_name.strip())

    attempts: list[str] = []

    # 1) Same first attempt as the working Colab script, but with a Shield-friendly timeout.
    try:
        return resource, resource.connect(timeout=RESOURCE_CONNECT_TIMEOUT_S)
    except Exception as exc:
        attempts.append("resource.connect: " + _redact(repr(exc), account_token))

    # 2) Windows fallback: try all reported connections, plus raw LAN IPs derived from plex.direct.
    conns = list(getattr(resource, "connections", None) or [])
    conns = [c for c in conns if _connection_uri(c)]
    conns = sorted(conns, key=_connection_score)
    tokens = _token_candidates(resource, account_token)

    for conn in conns:
        local = _safe_bool(getattr(conn, "local", False))
        relay = _safe_bool(getattr(conn, "relay", False))
        for uri in _candidate_uris(conn):
            for candidate in tokens:
                try:
                    plex = _make_plex(uri, candidate)
                    plex.library.sections()
                    attempts.append(f"OK: {uri} local={local} relay={relay}")
                    return resource, plex
                except Exception as exc:
                    msg = _redact(repr(exc), candidate)
                    attempts.append(f"FAIL: {uri} local={local} relay={relay}: {msg}")

    preview = "\n".join(attempts[-18:])
    raise RuntimeError(
        "Impossibile connettersi al server Plex. Tentativi effettuati:\n" + preview
    )


def _connect_main_colab_plus(token: str, server_name: str):
    _resource, plex = _connect_resource_colab_plus(token, server_name)
    return plex


def _get_plex_for_thread_shield_friendly(self):
    p = getattr(self.thread_local, "plex", None)
    if p is not None:
        return p
    if self.baseurl:
        try:
            try:
                p = PlexServer(self.baseurl, self.config.token, timeout=THREAD_CONNECT_TIMEOUT_S)
            except TypeError:
                p = PlexServer(self.baseurl, self.config.token)
        except Exception:
            p = self.plex_main
    else:
        p = self.plex_main
    self.thread_local.plex = p
    return p


def _fetch_item_xml_bundle_shield_friendly(self, item):
    rk = str(getattr(item, "ratingKey", "") or "")
    if rk:
        with self.mtx:
            if rk in self.xml_bundle_cache:
                self.metrics["xml_cache_hit"] += 1
                return self.xml_bundle_cache[rk]
    last_error = None
    for attempt in range(XML_RETRY_ATTEMPTS):
        if self.cancel_event.is_set():
            return None
        try:
            path = self._build_item_xml_query(item)
            with self.plex_http_guard():
                xml = self.get_plex_for_thread()._server.query(path)
            bundle = self._parse_xml_bundle(xml)
            with self.mtx:
                self.metrics["xml_fetch"] += 1
                if rk:
                    self.xml_bundle_cache[rk] = bundle
            return bundle
        except Exception as exc:
            last_error = exc
            time.sleep(XML_RETRY_BACKOFF_S * (2 ** attempt))
    self.log(f"[WARN] XML non letto per {getattr(item, 'title', '')}: {last_error!r}")
    return None


core_mod._connect_resource = _connect_resource_colab_plus
core_mod._connect_main = _connect_main_colab_plus
core_mod.InventoryRunner.get_plex_for_thread = _get_plex_for_thread_shield_friendly
core_mod.InventoryRunner.fetch_item_xml_bundle = _fetch_item_xml_bundle_shield_friendly
apply_debug_wide_patch(core_mod)


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


def _run_background_fixed(
    self,
    fn: Callable[[], Any],
    on_success: Callable[[Any], None],
    error_title: str,
    on_error: Callable[[str], None] | None = None,
) -> None:
    thread = QThread(self)
    worker = app_mod.GenericWorker(fn)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(on_success)
    worker.failed.connect(lambda tb: self._background_failed(error_title, tb, on_error=on_error))
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
    self.inventory_started_at = time.monotonic()
    self.eta_label.setText("Tempo: 00:00:00 | ETA residua: calcolo...")
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
