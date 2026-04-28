from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

APP_NAME = "PlexInventoryApp"
TOKEN_FILE = "tokens.json"


@dataclass
class SavedToken:
    label: str
    encrypted_token: str
    backend: str


def app_config_dir() -> Path:
    root = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if root:
        p = Path(root) / APP_NAME
    else:
        p = Path.home() / ".plex_inventory_app"
    p.mkdir(parents=True, exist_ok=True)
    return p


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data)
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob._buffer = buf  # keep alive
    return blob


def _blob_to_bytes(blob: _DATA_BLOB) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _dpapi_available() -> bool:
    return platform.system().lower() == "windows"


def _dpapi_protect(text: str) -> str:
    data = text.encode("utf-8")
    in_blob = _to_blob(data)
    out_blob = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        protected = _blob_to_bytes(out_blob)
        return base64.b64encode(protected).decode("ascii")
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(payload: str) -> str:
    protected = base64.b64decode(payload.encode("ascii"))
    in_blob = _to_blob(protected)
    out_blob = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return _blob_to_bytes(out_blob).decode("utf-8")
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(out_blob.pbData)


def _fallback_protect(text: str) -> str:
    # Non-Windows development fallback. On Windows the app uses DPAPI.
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _fallback_unprotect(payload: str) -> str:
    return base64.b64decode(payload.encode("ascii")).decode("utf-8")


class TokenStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (app_config_dir() / TOKEN_FILE)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_raw(self) -> dict:
        if not self.path.exists():
            return {"tokens": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"tokens": []}
            if "tokens" not in data or not isinstance(data["tokens"], list):
                data["tokens"] = []
            return data
        except Exception:
            return {"tokens": []}

    def _save_raw(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def labels(self) -> list[str]:
        data = self._load_raw()
        return sorted({str(t.get("label", "")).strip() for t in data["tokens"] if str(t.get("label", "")).strip()}, key=str.lower)

    def save(self, label: str, token: str) -> None:
        label = label.strip()
        token = token.strip()
        if not label:
            raise ValueError("Etichetta token vuota")
        if not token:
            raise ValueError("Token Plex vuoto")
        backend = "dpapi" if _dpapi_available() else "base64-dev"
        encrypted = _dpapi_protect(token) if backend == "dpapi" else _fallback_protect(token)
        data = self._load_raw()
        data["tokens"] = [t for t in data["tokens"] if str(t.get("label", "")).strip().lower() != label.lower()]
        data["tokens"].append({"label": label, "encrypted_token": encrypted, "backend": backend})
        self._save_raw(data)

    def load(self, label: str) -> str:
        label = label.strip()
        data = self._load_raw()
        for t in data["tokens"]:
            if str(t.get("label", "")).strip().lower() == label.lower():
                backend = t.get("backend", "")
                payload = t.get("encrypted_token", "")
                if backend == "dpapi":
                    return _dpapi_unprotect(payload)
                if backend == "base64-dev":
                    return _fallback_unprotect(payload)
                raise ValueError(f"Backend token non supportato: {backend}")
        raise KeyError(f"Token non trovato: {label}")

    def delete(self, label: str) -> None:
        label = label.strip()
        data = self._load_raw()
        data["tokens"] = [t for t in data["tokens"] if str(t.get("label", "")).strip().lower() != label.lower()]
        self._save_raw(data)
