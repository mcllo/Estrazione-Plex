from __future__ import annotations

import os
import tempfile
import traceback
import zipfile
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from plex_inventory_app.core import InventoryConfig, run_inventory

APP_NAME = "Plex Inventory Cloud Backend"
API_KEY_ENV = "PLEX_INVENTORY_API_KEY"

app = FastAPI(title=APP_NAME, version="0.1.0")


class InventoryRequest(BaseModel):
    token: str = Field(..., min_length=1)
    server_name: str = Field(..., min_length=1)
    library_names: list[str] = Field(default_factory=list)

    output_basename: str = "plex_inventory_cloud"
    run_preset: str = "FAST_PRECISE"
    max_workers: int = 8
    http_concurrency_fast: int = 3
    http_concurrency_slow: int = 1
    write_csv: bool = False
    write_xlsx: bool = True
    duration_output: str = "HMS"
    output_profile: str = "SLIM_BUDGET"
    debug: bool = False
    top_n_movies: Optional[int] = None
    top_n_shows: Optional[int] = None
    skip_short_clips: bool = True
    clip_min_seconds: int = 300


def _require_api_key(x_api_key: str | None) -> None:
    expected = os.environ.get(API_KEY_ENV, "").strip()
    if not expected:
        return
    if not x_api_key or x_api_key.strip() != expected:
        raise HTTPException(status_code=401, detail="API key non valida")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": APP_NAME}


@app.post("/inventory")
def inventory(req: InventoryRequest, x_api_key: str | None = Header(default=None)):
    _require_api_key(x_api_key)
    if not req.write_csv and not req.write_xlsx:
        raise HTTPException(status_code=400, detail="Seleziona almeno CSV o XLSX")

    tmpdir_obj = tempfile.TemporaryDirectory(prefix="plex_inventory_cloud_")
    tmpdir = Path(tmpdir_obj.name)

    try:
        cfg = InventoryConfig(
            token=req.token,
            server_name=req.server_name,
            library_names=req.library_names,
            output_dir=str(tmpdir),
            output_basename=req.output_basename or "plex_inventory_cloud",
            run_preset=req.run_preset,
            max_workers=req.max_workers,
            http_concurrency_fast=req.http_concurrency_fast,
            http_concurrency_slow=req.http_concurrency_slow,
            write_csv=req.write_csv,
            write_xlsx=req.write_xlsx,
            duration_output=req.duration_output,
            output_profile=req.output_profile,
            debug=req.debug,
            top_n_movies=req.top_n_movies,
            top_n_shows=req.top_n_shows,
            skip_short_clips=req.skip_short_clips,
            clip_min_seconds=req.clip_min_seconds,
        )

        logs: list[str] = []
        result = run_inventory(
            cfg,
            progress_callback=lambda done, total, msg: logs.append(f"{done}/{total} {msg}"),
            log_callback=lambda msg: logs.append(str(msg)),
            cancel_event=None,
        )

        files = []
        if result.csv_path:
            files.append(Path(result.csv_path))
        if result.xlsx_path:
            files.append(Path(result.xlsx_path))
        if not files:
            raise RuntimeError("Inventario completato ma nessun file creato")

        log_path = tmpdir / "cloud_run_log.txt"
        log_path.write_text("\n".join(logs), encoding="utf-8")
        files.append(log_path)

        zip_path = tmpdir / "plex_inventory_result.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in files:
                if file_path.exists():
                    zf.write(file_path, arcname=file_path.name)

        # Keep the temp dir alive until response is sent by attaching a close hook object.
        response = FileResponse(
            path=str(zip_path),
            media_type="application/zip",
            filename="plex_inventory_result.zip",
        )
        response.background = _CleanupBackground(tmpdir_obj)
        return response
    except HTTPException:
        tmpdir_obj.cleanup()
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        tmpdir_obj.cleanup()
        return JSONResponse(status_code=500, content={"error": str(exc), "traceback": tb})


class _CleanupBackground:
    def __init__(self, tmpdir_obj: tempfile.TemporaryDirectory) -> None:
        self.tmpdir_obj = tmpdir_obj

    async def __call__(self) -> None:
        self.tmpdir_obj.cleanup()
