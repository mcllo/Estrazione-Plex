from __future__ import annotations

from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import ipaddress
import json
import os
import pathlib
import re
import threading
import time
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

import requests

import pandas as pd

from plexapi.myplex import MyPlexAccount
from plexapi.library import MovieSection, ShowSection
from plexapi.server import PlexServer


ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]

TZ_MILAN = ZoneInfo("Europe/Rome")
TIMESTAMP_FMT = "%Y%m%d_%H%M%S"


@dataclass
class InventoryConfig:
    token: str
    server_name: str
    library_names: list[str] = field(default_factory=list)
    output_dir: str = "."
    output_basename: str = "plex_inventory_fast_slim"

    run_preset: str = "FAST_PRECISE"  # FAST_PRECISE | SLOW_PRECISE
    max_workers: int = 8
    http_concurrency_fast: int = 3
    http_concurrency_slow: int = 1

    write_csv: bool = False
    write_xlsx: bool = True
    duration_output: str = "HMS"  # HMS | BOTH
    output_profile: str = "SLIM_BUDGET"  # FULL | SLIM_RAW | SLIM_BUDGET

    fast_mode: bool = False
    hdr_xml_on_fast: bool = True
    xml_verify_video: bool = True
    video_verify_tol: float = 0.02
    debug: bool = False

    top_n_movies: Optional[int] = None
    top_n_shows: Optional[int] = None

    skip_short_clips: bool = True
    clip_min_seconds: int = 300

    def normalized(self) -> "InventoryConfig":
        self.run_preset = (self.run_preset or "FAST_PRECISE").strip().upper()
        if self.run_preset not in {"FAST_PRECISE", "SLOW_PRECISE"}:
            self.run_preset = "FAST_PRECISE"
        self.duration_output = (self.duration_output or "HMS").strip().upper()
        if self.duration_output not in {"HMS", "BOTH"}:
            self.duration_output = "HMS"
        self.output_profile = (self.output_profile or "SLIM_BUDGET").strip().upper()
        if self.output_profile not in {"FULL", "SLIM_RAW", "SLIM_BUDGET"}:
            self.output_profile = "SLIM_BUDGET"
        self.max_workers = max(1, int(self.max_workers or 1))
        self.http_concurrency_fast = max(1, int(self.http_concurrency_fast or 1))
        self.http_concurrency_slow = max(1, int(self.http_concurrency_slow or 1))
        if self.top_n_movies is not None and self.top_n_movies <= 0:
            self.top_n_movies = None
        if self.top_n_shows is not None and self.top_n_shows <= 0:
            self.top_n_shows = None
        return self


@dataclass
class InventoryResult:
    rows_created: int
    jobs_total: int
    jobs_done: int
    errors_total: int
    skipped_clips: int
    elapsed_seconds: float
    csv_path: Optional[str]
    xlsx_path: Optional[str]
    errors_preview: list[str]


def list_plex_servers(token: str) -> list[str]:
    account = MyPlexAccount(token=token.strip())
    names: list[str] = []
    for res in account.resources():
        # Plex can return clients and servers. A connectable Plex Media Server usually has provides=server.
        provides = str(getattr(res, "provides", "") or "").lower()
        product = str(getattr(res, "product", "") or "").lower()
        name = str(getattr(res, "name", "") or "").strip()
        if not name:
            continue
        if "server" in provides or "plex media server" in product:
            names.append(name)
    return sorted(set(names), key=str.lower)


def list_libraries(
    token: str,
    server_name: str,
    log_callback: LogCallback | None = None,
) -> list[dict[str, str]]:
    log = log_callback or (lambda _msg: None)
    log("Connessione al server Plex per lettura librerie...")
    log(f"Server richiesto: {server_name}")
    plex = _connect_main(token, server_name, log_callback=log_callback)
    log("Connessione riuscita, leggo sezioni libreria...")
    sections = plex.library.sections()
    log(f"Sezioni trovate: {len(sections)}")

    out: list[dict[str, str]] = []
    for sec in sections:
        sec_type = str(getattr(sec, "type", "") or "")
        if sec_type in ("movie", "show"):
            out.append({"title": sec.title, "type": sec_type})
    log(f"Librerie Movies/TV trovate: {len(out)}")
    return out


def _validate_plex_sections(base_url: str, token: str, session: requests.Session, timeout_s: int = 12) -> None:
    url = base_url.rstrip("/") + "/library/sections"
    response = session.get(
        url,
        params={"X-Plex-Token": token},
        timeout=(3, timeout_s),
        verify=False,
    )
    response.raise_for_status()


def _decoded_plex_direct_candidates(uri: str) -> list[str]:
    parsed = urlparse(uri)
    host = parsed.hostname or ""
    first = host.split(".", 1)[0]
    parts = first.split("-")
    if len(parts) != 4:
        return []
    try:
        if not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return []
    except Exception:
        return []
    ip = ".".join(parts)
    port = parsed.port or 32400
    return [f"http://{ip}:{port}", f"https://{ip}:{port}"]


def _is_private_lan_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def _safe_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _connection_uri(conn: object) -> str:
    return str(getattr(conn, "uri", "") or "").rstrip("/")


def _connection_score(conn: object) -> tuple[int, str]:
    uri = _connection_uri(conn)
    local = _safe_bool(getattr(conn, "local", False))
    relay = _safe_bool(getattr(conn, "relay", False))
    protocol = str(getattr(conn, "protocol", "") or "").lower()
    score = 0
    if local:
        score -= 100
    if relay:
        score += 50
    if protocol == "http":
        score -= 5
    if protocol == "https":
        score -= 2
    return score, uri


def _token_candidates(resource: object, account_token: str) -> list[str]:
    tokens: list[str] = []
    for attr in ("accessToken", "token"):
        value = str(getattr(resource, attr, "") or "").strip()
        if value and value not in tokens:
            tokens.append(value)
    clean_account_token = account_token.strip()
    if clean_account_token and clean_account_token not in tokens:
        tokens.append(clean_account_token)
    return tokens


def _connect_to_resource(token: str, server_name: str, log_callback: LogCallback | None = None):
    log = log_callback or (lambda _msg: None)
    account = MyPlexAccount(token=token.strip())
    resource = account.resource(server_name.strip())

    attempts: list[tuple[str, str]] = []

    log("Tentativo connessione legacy via resource.connect(timeout=12)")
    try:
        plex = resource.connect(timeout=12)
        plex.library.sections()
        log("Connessione legacy riuscita")
        return resource, plex
    except Exception as exc:
        attempts.append(("resource.connect(timeout=12)", type(exc).__name__))
        log(f"Connessione legacy fallita: {type(exc).__name__}")

    connections = list(getattr(resource, "connections", []) or [])
    sorted_connections = sorted(connections, key=_connection_score)

    session = requests.Session()
    session.verify = False
    token_candidates = _token_candidates(resource, token)

    structured_candidates: list[tuple[int, str, bool, bool, bool]] = []
    for conn in sorted_connections:
        uri = _connection_uri(conn).strip()
        if not uri:
            continue
        local = _safe_bool(getattr(conn, "local", False))
        relay = _safe_bool(getattr(conn, "relay", False))

        decoded_candidates = _decoded_plex_direct_candidates(uri)
        private_decoded_added = False
        for decoded in decoded_candidates:
            parsed_decoded = urlparse(decoded)
            host = parsed_decoded.hostname or ""
            is_private = _is_private_lan_ip(host)
            if is_private:
                structured_candidates.append((0, decoded, local, relay, True))
                private_decoded_added = True

        if local:
            structured_candidates.append((1, uri, local, relay, False))

        for decoded in decoded_candidates:
            parsed_decoded = urlparse(decoded)
            host = parsed_decoded.hostname or ""
            is_private = _is_private_lan_ip(host)
            if is_private and private_decoded_added:
                continue
            structured_candidates.append((2, decoded, local, relay, is_private))

        if not local:
            base_priority = 4 if relay else 3
            structured_candidates.append((base_priority, uri, local, relay, False))

    seen_urls: set[str] = set()
    private_attempted = False
    private_success = False
    for _priority, base_url, local, relay, is_private_candidate in sorted(structured_candidates, key=lambda t: t[0]):
        if base_url in seen_urls:
            continue
        seen_urls.add(base_url)
        if is_private_candidate:
            private_attempted = True
        log(f"Tentativo URI: {base_url} local={local} relay={relay} private={is_private_candidate}")
        for candidate_token in token_candidates:
            try:
                plex = PlexServer(base_url, candidate_token, timeout=12, session=session)
                _validate_plex_sections(base_url, candidate_token, session=session, timeout_s=12)
                if is_private_candidate:
                    private_success = True
                log(f"Connessione riuscita su URI: {base_url}")
                return resource, plex
            except Exception as exc:
                attempts.append((base_url, type(exc).__name__))
                log(f"Tentativo fallito: {base_url} local={local} relay={relay} private={is_private_candidate}: {type(exc).__name__}")

    detail_lines = [f"- {uri}: {err_type}" for uri, err_type in attempts[-20:]]
    if not detail_lines:
        detail_lines = ["- nessun tentativo registrato"]
    private_note = ""
    if private_attempted and not private_success:
        private_note = " Nessuna connessione LAN/private funzionante trovata."
    details = "\n".join(detail_lines)
    raise RuntimeError(
        f"Impossibile connettersi al server Plex '{server_name}'.{private_note} Tentativi effettuati:\
{details}"
    )


def _connect_main(token: str, server_name: str, log_callback: LogCallback | None = None) -> PlexServer:
    _resource, plex = _connect_to_resource(token, server_name, log_callback=log_callback)
    return plex


def _connect_resource(token: str, server_name: str, log_callback: LogCallback | None = None):
    return _connect_to_resource(token, server_name, log_callback=log_callback)


def with_timestamp(path_str: str, fmt: str = TIMESTAMP_FMT, tz=TZ_MILAN) -> str:
    p = pathlib.Path(path_str)
    ts = datetime.now(tz).strftime(fmt)
    return str(p.with_name(f"{p.stem}_{ts}{p.suffix}"))


def build_output_columns(output_profile: str, duration_output: str, include_audit: bool = False) -> list[str]:
    full = [
        "type", "title_or_series", "season", "episode", "episode_title", "year",
        "added_at_milan", "resolution", "hdr", "videoCodec", "container",
        "bitrate_mbps_total", "bitrate_total_source", "bitrate_mbps_video",
        "bitrate_mbps_video_est", "bitrate_mbps_video_final",
        "audio_bitrate_total_mbps_raw", "secondary_video_mbps_raw", "container_overhead_mbps_raw",
        "audio_bitrate_total_mbps", "secondary_video_mbps", "container_overhead_mbps",
        "size_gib", "imdb_id", "imdb_rating", "tmdb_id", "rating_key", "genres",
        "file", "audio_it_bitrate_mbps", "audio_it_quality", "audio_en_bitrate_mbps", "audio_en_quality",
    ]
    slim = [
        "type", "title_or_series", "season", "episode", "episode_title", "year",
        "added_at_milan", "resolution", "hdr", "videoCodec", "container",
        "bitrate_mbps_total", "bitrate_mbps_video",
        "audio_it_bitrate_mbps", "audio_it_quality", "audio_en_bitrate_mbps", "audio_en_quality",
        "size_gib", "imdb_id", "imdb_rating", "tmdb_id", "rating_key", "genres", "file",
    ]
    if include_audit:
        full += [
            "media_id", "part_id", "part_match_source", "resolution_source", "duration_source",
            "bitrate_total_calc_mbps", "bitrate_total_xml_mbps", "bitrate_total_xml_rejected_reason",
            "bitrate_video_source",
        ]
    target = full if output_profile == "FULL" else slim + ([
        "media_id", "part_id", "part_match_source", "resolution_source", "duration_source",
        "bitrate_total_source", "bitrate_total_calc_mbps", "bitrate_total_xml_mbps", "bitrate_total_xml_rejected_reason",
        "bitrate_video_source",
    ] if include_audit and output_profile != "FULL" else [])
    marker = "bitrate_mbps_total"
    idx = target.index(marker)
    if duration_output == "BOTH":
        target.insert(idx, "duration_s")
        target.insert(idx, "duration_hms")
    else:
        target.insert(idx, "duration_hms")
    return target


class StreamProxy:
    def __init__(self, attrib: dict):
        self._data = SimpleNamespace(attrib=dict(attrib))
        try:
            self.streamType = int(attrib.get("streamType", None))
        except Exception:
            self.streamType = None


class InventoryRunner:
    OVERHEAD_BASE = {
        "mkv": 0.20,
        "mp4": 0.15,
        "m4v": 0.15,
        "m2ts": 1.00, "m2t": 1.00, "ts": 1.00, "mpegts": 1.00,
    }
    OVERHEAD_PER_SUBTITLE = 0.005
    OVERHEAD_PER_EXTRA_AUDIO = 0.07
    CLIP_CONTAINERS = {"mpegts", "m2ts", "m2t", "ts"}

    _LANG_IT = {"it", "ita", "it-it", "italiano", "italian", "italian (italy)", "it_it"}
    _LANG_EN = {"en", "eng", "en-us", "en-gb", "english", "inglese", "en_us", "en_gb"}
    _ITA_PAT = re.compile(r"(?i)(?:\bitaliano\b|\bitalian\b|\bita\b|\[(?:ita|ital)\])")
    _ENG_PAT = re.compile(r"(?i)(?:\benglish\b|\binglese\b|\beng\b|\[(?:eng)\])")
    _FILE_IT_PAT = re.compile(r"(?i)(?<![a-z])(?:ita|ital|italian|italiano)(?![a-z])")
    _FILE_EN_PAT = re.compile(r"(?i)(?<![a-z])(?:eng|english)(?![a-z])|\binglese\b")

    def __init__(
        self,
        config: InventoryConfig,
        progress_callback: Optional[ProgressCallback] = None,
        log_callback: Optional[LogCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        self.config = config.normalized()
        self.progress_callback = progress_callback or (lambda done, total, msg: None)
        self.log = log_callback or (lambda msg: None)
        self.cancel_event = cancel_event or threading.Event()

        self.rows: list[dict] = []
        self.debug_rows: list[dict] = []
        self.debug_stream_rows: list[dict] = []

        self.mtx = threading.Lock()
        self.xml_bundle_cache: dict[str, dict] = {}
        self.streams_cache: dict[tuple, list] = {}
        self.duration_cache: dict[str, int] = {}
        self.duration_source_cache: dict[str, str] = {}
        self.show_meta: dict[str, dict] = {}
        self.show_meta_lock = threading.Lock()
        self.reloaded_items: set[str] = set()
        self.thread_local = threading.local()

        self.metrics = {
            "xml_fetch": 0,
            "xml_cache_hit": 0,
            "streams_cache_hit": 0,
            "duration_cache_hit": 0,
            "jobs_total": 0,
            "jobs_done": 0,
            "rows_created": 0,
            "errors_total": 0,
            "rows_skipped_clip": 0,
        }
        self.errors_preview: list[str] = []

        self.resource = None
        self.plex_main: Optional[PlexServer] = None
        self.baseurl: Optional[str] = None
        self.plex_http_sem = threading.BoundedSemaphore(1)

        self.output_columns = build_output_columns(
            self.config.output_profile,
            self.config.duration_output,
            include_audit=(self.config.output_profile == "FULL" or self.config.debug),
        )

    def run(self) -> InventoryResult:
        t0 = time.time()
        cfg = self.config
        output_dir = pathlib.Path(cfg.output_dir or ".").expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        self.log("Connessione a Plex...")
        self.resource, self.plex_main = _connect_resource(cfg.token, cfg.server_name)
        self.baseurl = self._infer_baseurl(self.plex_main, self.resource)

        http_concurrency = cfg.http_concurrency_fast if cfg.run_preset == "FAST_PRECISE" else cfg.http_concurrency_slow
        if cfg.run_preset == "FAST_PRECISE" and (not self.baseurl) and http_concurrency > 1:
            self.log("[WARN] BASEURL non rilevato: imposto HTTP_CONCURRENCY=1 per evitare race su sessione HTTP.")
            http_concurrency = 1
        self.plex_http_sem = threading.BoundedSemaphore(max(1, int(http_concurrency)))

        jobs = self._build_jobs()
        self.metrics["jobs_total"] = len(jobs)
        self.progress_callback(0, len(jobs), "Jobs Plex preparati")
        self.log(f"Parti da elaborare: {len(jobs)}")

        errors_total = 0
        with ThreadPoolExecutor(max_workers=cfg.max_workers) as executor:
            future_to_job = {executor.submit(self.add_row_from_part, *job): job for job in jobs}
            for fut in as_completed(future_to_job):
                if self.cancel_event.is_set():
                    # Try to cancel queued jobs. Running jobs will complete.
                    for pending in future_to_job:
                        pending.cancel()
                try:
                    fut.result()
                except Exception as exc:
                    errors_total += 1
                    msg = self._format_job_error(future_to_job.get(fut), exc)
                    if len(self.errors_preview) < 20:
                        self.errors_preview.append(msg)
                    self.log(f"[ERROR] {msg}")
                    with self.mtx:
                        self.metrics["jobs_done"] += 1
                finally:
                    with self.mtx:
                        done = int(self.metrics["jobs_done"])
                        total = int(self.metrics["jobs_total"])
                    self.progress_callback(done, total, f"Elaborate {done}/{total}")
                if self.cancel_event.is_set():
                    self.log("Esecuzione interrotta dall'utente. Salvo le righe già prodotte.")
                    break

        elapsed = time.time() - t0
        with self.mtx:
            self.metrics["errors_total"] = errors_total

        csv_path, xlsx_path = self._save_outputs(output_dir)
        self._log_final_report(elapsed, csv_path, xlsx_path)

        return InventoryResult(
            rows_created=int(self.metrics["rows_created"]),
            jobs_total=int(self.metrics["jobs_total"]),
            jobs_done=int(self.metrics["jobs_done"]),
            errors_total=int(self.metrics["errors_total"]),
            skipped_clips=int(self.metrics["rows_skipped_clip"]),
            elapsed_seconds=elapsed,
            csv_path=csv_path,
            xlsx_path=xlsx_path,
            errors_preview=list(self.errors_preview),
        )

    def _infer_baseurl(self, plex_obj, resource) -> Optional[str]:
        for attr in ("_baseurl", "baseurl", "_url"):
            value = getattr(plex_obj, attr, None)
            if value:
                return value
        srv = getattr(plex_obj, "_server", None)
        if srv is not None:
            for attr in ("_baseurl", "baseurl", "_url"):
                value = getattr(srv, attr, None)
                if value:
                    return value
        try:
            for conn in getattr(resource, "connections", None) or []:
                uri = getattr(conn, "uri", None)
                if uri:
                    return uri
        except Exception:
            pass
        return None

    @contextmanager
    def plex_http_guard(self):
        self.plex_http_sem.acquire()
        try:
            yield
        finally:
            self.plex_http_sem.release()

    def get_plex_for_thread(self):
        p = getattr(self.thread_local, "plex", None)
        if p is not None:
            return p
        if self.baseurl:
            try:
                try:
                    p = PlexServer(self.baseurl, self.config.token, timeout=6)
                except TypeError:
                    p = PlexServer(self.baseurl, self.config.token)
            except Exception:
                p = self.plex_main
        else:
            p = self.plex_main
        self.thread_local.plex = p
        return p

    def _build_jobs(self) -> list[tuple]:
        assert self.plex_main is not None
        cfg = self.config
        sections = self.plex_main.library.sections()
        if cfg.library_names:
            wanted = {n.strip().lower() for n in cfg.library_names if n.strip()}
            selected = [s for s in sections if s.title.strip().lower() in wanted]
        else:
            selected = [s for s in sections if getattr(s, "type", "") in ("movie", "show")]

        jobs = []
        for sec in selected:
            if self.cancel_event.is_set():
                break
            self.log(f"Libreria: {sec.title} ({getattr(sec, 'type', '')})")
            if isinstance(sec, MovieSection) or getattr(sec, "type", "") == "movie":
                items = list(sec.all())
                if cfg.top_n_movies:
                    items = items[: cfg.top_n_movies]
                for movie in items:
                    try:
                        for media in movie.media or []:
                            for part in media.parts or []:
                                jobs.append((movie, media, part, "Movie"))
                    except Exception:
                        continue

            elif isinstance(sec, ShowSection) or getattr(sec, "type", "") == "show":
                shows = list(sec.all())
                if cfg.top_n_shows:
                    shows = shows[: cfg.top_n_shows]

                for i, show in enumerate(shows, start=1):
                    if self.cancel_event.is_set():
                        break
                    try:
                        self.cache_show_meta(show)
                    except Exception:
                        pass
                    try:
                        for ep in show.episodes():
                            for media in ep.media or []:
                                for part in media.parts or []:
                                    jobs.append((ep, media, part, "TV"))
                    except Exception as exc:
                        self.log(f"[WARN] Episodi non letti per {getattr(show, 'title', '')}: {exc!r}")
                    if i % 10 == 0:
                        self.progress_callback(0, 1, f"Scansione serie: {i}/{len(shows)}")
        return jobs

    # ---------- Generic helpers ----------

    def norm_res(self, media) -> str:
        s = str(getattr(media, "videoResolution", "") or "").strip().lower()
        if s in ("2160", "2160p", "4k", "uhd"):
            return "2160p"
        if s in ("1440", "1440p", "qhd"):
            return "1440p"
        if s in ("1080", "1080p", "fhd"):
            return "1080p"
        if s in ("720", "720p", "hd"):
            return "720p"
        if s in ("sd",):
            return "SD"
        return s.upper() if s else ""

    def detect_resolution(self, item=None, media=None, part=None):
        v_streams = self.get_video_streams(item, part) if (item is not None and part is not None) else []
        primary = self.select_primary_video_stream(v_streams)
        h = self.get_int(primary, "height", None) or self.get_int(primary, "codedHeight", None)
        if h and h > 0:
            if h >= 2000:
                return "2160p", "stream_height"
            if h >= 1400:
                return "1440p", "stream_height"
            if h >= 1000:
                return "1080p", "stream_height"
            if h >= 700:
                return "720p", "stream_height"
            return "SD", "stream_height"
        mh = getattr(media, "height", None)
        try:
            mh = int(mh) if mh is not None else None
        except Exception:
            mh = None
        if mh and mh > 0:
            if mh >= 2000:
                return "2160p", "media_height"
            if mh >= 1400:
                return "1440p", "media_height"
            if mh >= 1000:
                return "1080p", "media_height"
            if mh >= 700:
                return "720p", "media_height"
            return "SD", "media_height"
        nr = self.norm_res(media)
        return (nr, "media_videoResolution") if nr else ("", "unknown")

    @staticmethod
    def kbps_to_mbps(x):
        try:
            return float(x) / 1000.0
        except Exception:
            return None

    @staticmethod
    def parse_required_bandwidths_first_mbps(val):
        if not val:
            return None
        m = re.match(r"\s*([0-9]+)", str(val))
        if not m:
            return None
        try:
            return float(m.group(1)) / 1000.0
        except Exception:
            return None

    @staticmethod
    def get_attr(st, name, default=None):
        if st is None:
            return default
        v = getattr(st, name, None)
        if v is not None:
            return v
        if hasattr(st, "_data") and hasattr(st._data, "attrib"):
            return st._data.attrib.get(name, default)
        if hasattr(st, "get"):
            try:
                return st.get(name, default)
            except Exception:
                return default
        return default

    def get_int(self, st, name, default=None):
        v = self.get_attr(st, name, None)
        try:
            return int(v)
        except Exception:
            try:
                return int(float(v))
            except Exception:
                return default

    @staticmethod
    def format_duration_hms(seconds):
        if seconds is None:
            return None
        try:
            sec = int(round(float(seconds)))
        except Exception:
            return None
        sec = max(0, sec)
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def get_series_title_for_episode(self, ep) -> str:
        title = (getattr(ep, "grandparentTitle", "") or "").strip()
        if title:
            return title
        show_fn = getattr(ep, "show", None)
        if callable(show_fn):
            try:
                sh = show_fn()
                return (getattr(sh, "title", "") or "").strip()
            except Exception:
                return ""
        return ""

    @staticmethod
    def json_dumps_safe(obj) -> str:
        def _default(o):
            try:
                return str(o)
            except Exception:
                return repr(o)
        try:
            return json.dumps(obj, ensure_ascii=False, default=_default)
        except Exception:
            return str(obj)

    @staticmethod
    def clip_excel_cell(s, limit=32000):
        if s is None:
            return None
        s = str(s)
        if len(s) <= limit:
            return s
        suffix = f" ...[TRUNCATED len={len(s)}]"
        return s[: max(0, limit - len(suffix))] + suffix

    def obj_attribs_dict(self, obj) -> dict:
        d = {}
        try:
            if hasattr(obj, "_data") and hasattr(obj._data, "attrib") and isinstance(obj._data.attrib, dict):
                d.update(dict(obj._data.attrib))
        except Exception:
            pass
        extra_keys = [
            "key", "ratingKey", "guid", "type", "title", "originalTitle", "grandparentTitle",
            "parentTitle", "parentIndex", "index", "year", "addedAt", "updatedAt", "duration",
            "viewCount", "lastViewedAt", "studio", "contentRating", "summary", "id", "container",
            "bitrate", "size", "file", "width", "height", "videoCodec", "audioCodec",
            "videoFrameRate", "videoResolution", "aspectRatio", "videoDynamicRange", "optimizedForStreaming",
        ]
        for k in extra_keys:
            if k in d:
                continue
            try:
                v = getattr(obj, k, None)
            except Exception:
                v = None
            if v is not None:
                d[k] = v
        return d

    def stream_attribs_dict(self, st) -> dict:
        d = {}
        try:
            if hasattr(st, "_data") and hasattr(st._data, "attrib") and isinstance(st._data.attrib, dict):
                d.update(dict(st._data.attrib))
        except Exception:
            pass
        extra_keys = [
            "id", "index", "streamIdentifier", "streamType", "codec", "profile", "bitrate", "channels",
            "audioChannelCount", "channelLayout", "samplingRate", "bitDepth", "language", "languageCode",
            "title", "displayTitle", "extendedDisplayTitle", "selected", "default", "decision",
        ]
        for k in extra_keys:
            if k in d:
                continue
            try:
                v = getattr(st, k, None)
            except Exception:
                v = None
            if v is not None:
                d[k] = v
        return d

    # ---------- XML bundle ----------

    def _build_item_xml_query(self, item) -> str:
        q = f"{item.key}?includeAllStreams=1"
        if self.config.run_preset == "FAST_PRECISE":
            q += "&includeGuids=1"
        return q

    def _parse_xml_bundle(self, xml_root):
        bundle = {
            "guids": {"imdb_id": None, "tmdb_id": None},
            "imdb_rating": None,
            "genres": "",
            "parts": {},
            "by_file": {},
            "by_base": {},
        }
        try:
            for g in xml_root.iter("Guid"):
                gid = g.get("id") or ""
                if gid.startswith("imdb://") and bundle["guids"]["imdb_id"] is None:
                    bundle["guids"]["imdb_id"] = gid.split("imdb://", 1)[1]
                elif gid.startswith("tmdb://") and bundle["guids"]["tmdb_id"] is None:
                    bundle["guids"]["tmdb_id"] = gid.split("tmdb://", 1)[1]
                if bundle["guids"]["imdb_id"] and bundle["guids"]["tmdb_id"]:
                    break
        except Exception:
            pass
        try:
            for r in xml_root.iter("Rating"):
                img = (r.get("image") or "").lower()
                val = r.get("value")
                if "imdb" in img and val is not None:
                    try:
                        bundle["imdb_rating"] = float(val)
                        break
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            genres = []
            seen = set()
            for ge in xml_root.iter("Genre"):
                tag = ge.get("tag") or ge.get("title") or ge.get("name")
                if tag:
                    tag = str(tag).strip()
                    if tag and tag not in seen:
                        seen.add(tag)
                        genres.append(tag)
            bundle["genres"] = "|".join(genres) if genres else ""
        except Exception:
            pass
        try:
            for md in xml_root.iter("Media"):
                md_attrib = dict(getattr(md, "attrib", {}) or {})
                for prt in md.iter("Part"):
                    prt_attrib = dict(getattr(prt, "attrib", {}) or {})
                    pid = str(prt_attrib.get("id") or "")
                    pfile = prt_attrib.get("file") or ""
                    pbase = os.path.basename(pfile) if pfile else ""
                    streams = [StreamProxy(st.attrib) for st in prt.iter("Stream")]
                    key = pid if pid else (pfile or prt_attrib.get("key") or "")
                    if not key:
                        continue
                    bundle["parts"][key] = {"media": md_attrib, "part": prt_attrib, "streams": streams}
                    if pfile:
                        bundle["by_file"].setdefault(pfile, []).append(key)
                    if pbase:
                        bundle["by_base"].setdefault(pbase, []).append(key)
        except Exception:
            pass
        return bundle

    def fetch_item_xml_bundle(self, item):
        rk = str(getattr(item, "ratingKey", "") or "")
        if rk:
            with self.mtx:
                if rk in self.xml_bundle_cache:
                    self.metrics["xml_cache_hit"] += 1
                    return self.xml_bundle_cache[rk]
        last_error = None
        for attempt in range(4):
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
                time.sleep(0.35 * (2 ** attempt))
        self.log(f"[WARN] XML non letto per {getattr(item, 'title', '')}: {last_error!r}")
        return None

    def _xml_match_score(self, info, media, part):
        score = 0
        part_xml = (info or {}).get("part", {}) or {}
        media_xml = (info or {}).get("media", {}) or {}
        if str(part_xml.get("key") or "") and str(part_xml.get("key")) == str(getattr(part, "key", "") or ""):
            score += 4
        if str(media_xml.get("id") or "") and str(media_xml.get("id")) == str(getattr(media, "id", "") or ""):
            score += 4
        for attr, pts in (("size", 3), ("duration", 3), ("container", 2)):
            xml_v = part_xml.get(attr) or media_xml.get(attr)
            plex_v = getattr(part, attr, None) if hasattr(part, attr) else getattr(media, attr, None)
            if xml_v is not None and plex_v is not None and str(xml_v) == str(plex_v):
                score += pts
        return score

    def find_part_info_from_bundle(self, item, part, return_source=False):
        bundle = self.fetch_item_xml_bundle(item)
        if not bundle:
            return (None, "xml_missing") if return_source else None
        pid = str(getattr(part, "id", "") or "")
        pfile = getattr(part, "file", "") or ""
        pbase = os.path.basename(pfile) if pfile else ""
        media = getattr(part, "_parent", None)
        if pid and pid in bundle["parts"]:
            out = bundle["parts"][pid]
            return (out, "xml_part_id") if return_source else out
        if pfile and pfile in bundle["by_file"]:
            keys = bundle["by_file"].get(pfile) or []
            if len(keys) == 1:
                out = bundle["parts"].get(keys[0])
                return (out, "xml_file_unique") if return_source else out
            if len(keys) > 1:
                scored = []
                for k in keys:
                    info = bundle["parts"].get(k)
                    if info:
                        scored.append((self._xml_match_score(info, media, part), info))
                scored = sorted(scored, key=lambda x: x[0], reverse=True)
                if scored and len(scored) == 1 or (len(scored) > 1 and scored[0][0] > 0 and scored[0][0] > scored[1][0]):
                    out = scored[0][1]
                    return (out, "xml_file_disambiguated") if return_source else out
                return (None, "xml_ambiguous") if return_source else None
        if pbase and pbase in bundle["by_base"]:
            keys = bundle["by_base"].get(pbase) or []
            if len(keys) == 1:
                out = bundle["parts"].get(keys[0])
                return (out, "xml_basename_unique") if return_source else out
            return (None, "xml_ambiguous") if return_source else None
        return (None, "xml_missing") if return_source else None

    # ---------- Streams ----------

    def _streams_cache_key(self, item, part):
        pid = getattr(part, "id", None)
        if pid is not None:
            return ("id", pid)
        return ("file", getattr(item, "ratingKey", None), getattr(part, "file", None))

    def safe_streams(self, item, part):
        key = self._streams_cache_key(item, part)
        if key in self.streams_cache:
            with self.mtx:
                self.metrics["streams_cache_hit"] += 1
            return self.streams_cache[key]

        streams = []
        xml_streams_first = self.config.run_preset == "FAST_PRECISE"
        enable_item_reload = self.config.run_preset == "SLOW_PRECISE"
        enable_part_reload = self.config.run_preset == "SLOW_PRECISE"

        if xml_streams_first:
            info = self.find_part_info_from_bundle(item, part)
            if info and info.get("streams"):
                streams = info["streams"]

        if not streams:
            if enable_item_reload:
                try:
                    rk = str(getattr(item, "ratingKey", "") or "")
                    do_reload = False
                    if rk:
                        with self.mtx:
                            if rk not in self.reloaded_items:
                                self.reloaded_items.add(rk)
                                do_reload = True
                    if do_reload:
                        with self.plex_http_guard():
                            item.reload(includeAllStreams=1, includeGuids=1)
                except Exception:
                    pass
            try:
                streams = list(getattr(part, "streams", []) or [])
            except Exception:
                if enable_part_reload:
                    try:
                        with self.plex_http_guard():
                            part.reload()
                        streams = list(getattr(part, "streams", []) or [])
                    except Exception:
                        streams = []
            if not streams:
                info = self.find_part_info_from_bundle(item, part)
                if info and info.get("streams"):
                    streams = info["streams"]
        if streams:
            self.streams_cache[key] = streams
        return streams

    def stream_bitrate_mbps(self, stream):
        if stream is None:
            return None
        bitrate = self.get_attr(stream, "bitrate", None)
        mbps = self.kbps_to_mbps(bitrate) if bitrate else None
        if mbps and mbps > 0:
            return mbps
        rb = self.get_attr(stream, "requiredBandwidths", None)
        mbps2 = self.parse_required_bandwidths_first_mbps(rb)
        if mbps2 and mbps2 > 0:
            return mbps2
        return None

    def media_total_mbps_via_xml(self, item, part, xml_info=None):
        info = xml_info if xml_info is not None else self.find_part_info_from_bundle(item, part)
        if not info:
            return None
        try:
            mb = info["media"].get("bitrate")
            return float(mb) / 1000.0 if mb else None
        except Exception:
            return None

    def fetch_video_bitrate_via_xml(self, item, part, xml_info=None):
        info = xml_info if xml_info is not None else self.find_part_info_from_bundle(item, part)
        if not info:
            return None
        try:
            for st in info.get("streams", []) or []:
                if getattr(st, "streamType", None) == 1:
                    b = self.get_attr(st, "bitrate", None)
                    if b:
                        return float(b) / 1000.0
        except Exception:
            pass
        return None

    # ---------- HDR, IDs, genres ----------

    def detect_hdr_robusto(self, item, media, part):
        vdr = (getattr(media, "videoDynamicRange", "") or "").strip().upper()
        if vdr == "DOLBY VISION":
            return "DV"
        if vdr == "HDR10+":
            return "HDR10+"
        if vdr == "HDR10":
            return "HDR10"
        if vdr == "HLG":
            return "HLG"
        if vdr == "SDR":
            return "SDR"
        try:
            for st in self.safe_streams(item, part):
                if getattr(st, "streamType", None) == 1:
                    for attr in ("DOVIPresent", "DOVIBLPresent", "DoViPresent", "doviPresent", "dv_profile", "DVProfile", "HdrFormat", "hdr"):
                        val = self.get_attr(st, attr, None)
                        if val:
                            sval = str(val).lower()
                            if ("dolby" in sval) or ("vision" in sval) or ("dovi" in sval) or (sval in ("1", "true")):
                                return "DV"
                    for attr in ("hdr10Plus", "HDR10Plus", "hdr10plus"):
                        val = self.get_attr(st, attr, None)
                        if val and str(val).lower() in ("1", "true", "hdr10+", "hdr10plus"):
                            return "HDR10+"
                    trc = str(self.get_attr(st, "colorTrc", "") or "").lower()
                    if "smpte2084" in trc or "pq" in trc:
                        return "HDR10"
                    if "arib-std-b67" in trc or "hlg" in trc:
                        return "HLG"
        except Exception:
            pass
        info = self.find_part_info_from_bundle(item, part)
        if info:
            vdr2 = (info["media"].get("videoDynamicRange") or "").upper()
            if "DOLBY VISION" in vdr2:
                return "DV"
            if "HDR10+" in vdr2:
                return "HDR10+"
            if "HDR10" in vdr2:
                return "HDR10"
            if "HLG" in vdr2:
                return "HLG"
            if "SDR" in vdr2:
                return "SDR"
        return "SDR"

    def get_ids_and_rating(self, entity):
        imdb_id = None
        imdb_rating = None
        tmdb_id = None
        try:
            for g in getattr(entity, "guids", None) or []:
                gid = getattr(g, "id", "") or ""
                if gid.startswith("imdb://"):
                    imdb_id = gid.split("imdb://", 1)[1]
                if gid.startswith("tmdb://"):
                    tmdb_id = gid.split("tmdb://", 1)[1]
            ai = (getattr(entity, "audienceRatingImage", "") or "").lower()
            ri = (getattr(entity, "ratingImage", "") or "").lower()
            if "imdb" in ai and getattr(entity, "audienceRating", None) is not None:
                imdb_rating = float(entity.audienceRating)
            elif "imdb" in ri and getattr(entity, "rating", None) is not None:
                imdb_rating = float(entity.rating)
        except Exception:
            pass
        return imdb_id, imdb_rating, tmdb_id

    def get_ids_rating_from_xml(self, item):
        bundle = self.fetch_item_xml_bundle(item)
        if not bundle:
            return None, None, None
        return (
            bundle.get("guids", {}).get("imdb_id"),
            bundle.get("imdb_rating"),
            bundle.get("guids", {}).get("tmdb_id"),
        )

    def get_genres_from_xml(self, item):
        bundle = self.fetch_item_xml_bundle(item)
        if not bundle:
            return ""
        return bundle.get("genres") or ""

    @staticmethod
    def get_genres_from_entity(entity):
        try:
            genres = [gx.tag for gx in getattr(entity, "genres", None) or []]
            return "|".join(genres) if genres else ""
        except Exception:
            return ""

    def cache_show_meta(self, show):
        rk = str(getattr(show, "ratingKey", "") or "")
        if not rk:
            return
        with self.show_meta_lock:
            if rk in self.show_meta:
                return
        gens = self.get_genres_from_xml(show) or self.get_genres_from_entity(show)
        sid, srat, stmdb = self.get_ids_and_rating(show)
        try:
            need_reload = (not gens) or (sid is None and stmdb is None)
            if need_reload:
                with self.plex_http_guard():
                    show.reload(includeGuids=1)
                gens = self.get_genres_from_xml(show) or self.get_genres_from_entity(show) or gens
                sid2, srat2, stmdb2 = self.get_ids_and_rating(show)
                sid = sid or sid2
                stmdb = stmdb or stmdb2
                srat = srat if srat is not None else srat2
        except Exception:
            pass
        with self.show_meta_lock:
            self.show_meta[rk] = {
                "title": (getattr(show, "title", "") or "").strip(),
                "genres": gens or "",
                "imdb_id": sid,
                "imdb_rating": srat,
                "tmdb_id": stmdb,
            }

    def get_show_meta_for_episode(self, ep):
        rk = str(getattr(ep, "grandparentRatingKey", "") or "")
        if not rk:
            return None
        with self.show_meta_lock:
            return self.show_meta.get(rk)

    # ---------- Stream helpers ----------

    def get_video_streams(self, item, part):
        return [st for st in self.safe_streams(item, part) if getattr(st, "streamType", None) == 1]

    def get_audio_streams(self, item, part):
        return [st for st in self.safe_streams(item, part) if getattr(st, "streamType", None) == 2]

    def get_subtitle_streams(self, item, part):
        return [st for st in self.safe_streams(item, part) if getattr(st, "streamType", None) == 3]

    def select_primary_video_stream(self, streams):
        def hw(st):
            h = self.get_int(st, "height", None) or self.get_int(st, "codedHeight", None) or 0
            w = self.get_int(st, "width", None) or self.get_int(st, "codedWidth", None) or 0
            return int(h), int(w)
        if not streams:
            return None
        return sorted(streams, key=hw, reverse=True)[0]

    # ---------- Duration ----------

    def robust_duration_ms(self, item, media=None, part=None, xml_info=None, part_match_source="xml_missing"):
        pf = getattr(part, "file", None)
        if pf in self.duration_cache:
            with self.mtx:
                self.metrics["duration_cache_hit"] += 1
            return self.duration_cache[pf]
        def _ok(d):
            try:
                dv = int(float(d))
            except Exception:
                return None
            if dv <= 0:
                return None
            kind = (getattr(item, "type", "") or "").lower()
            if kind in {"movie", "episode"} and dv < 60000:
                return None
            return dv
        if xml_info and part_match_source in {"xml_part_id", "xml_file_unique", "xml_file_disambiguated", "xml_basename_unique"}:
            d = _ok((xml_info.get("part", {}) or {}).get("duration"))
            if d:
                self.duration_cache[pf] = d; self.duration_source_cache[pf] = "xml_part"; return d
            d = _ok((xml_info.get("media", {}) or {}).get("duration"))
            if d:
                self.duration_cache[pf] = d; self.duration_source_cache[pf] = "xml_media"; return d
        for src, lbl in ((getattr(part, "duration", None), "plex_part"), (getattr(media, "duration", None), "plex_media"), (getattr(item, "duration", None), "plex_item")):
            d = _ok(src)
            if d:
                self.duration_cache[pf] = d
                self.duration_source_cache[pf] = lbl
                return d
        try:
            container = (getattr(part, "container", "") or "").lower()
            if container in {"mpegts", "m2ts", "m2t", "ts"}:
                size_bytes = int(getattr(part, "size", 0) or 0)
                mbps = self.media_total_mbps_via_xml(item, part)
                if size_bytes > 0 and mbps and mbps > 0:
                    est_s = (size_bytes * 8.0 / 1_000_000.0) / max(mbps, 1e-3)
                    if 1800 <= est_s <= 18000:
                        self.duration_cache[pf] = int(est_s * 1000)
                        self.duration_source_cache[pf] = "xml_size_fix"
                        return self.duration_cache[pf]
        except Exception:
            pass
        self.duration_cache[pf] = int(getattr(item, "duration", 0) or 0)
        self.duration_source_cache[pf] = "fallback_item" if self.duration_cache[pf] else "missing"
        return self.duration_cache[pf]

    # ---------- Audio quality ----------

    def _normalize_lang_string(self, s: str):
        if not s:
            return None
        x = str(s).strip().lower()
        if x in self._LANG_IT or x.startswith("it"):
            return "it"
        if x in self._LANG_EN or x.startswith("en"):
            return "en"
        if x in {"italiano", "italian"}:
            return "it"
        if x in {"english", "inglese"}:
            return "en"
        return None

    def stream_lang_code(self, st):
        for k in ("languageCode", "languageTag", "language", "audioLanguage", "audioLocale"):
            v = self.get_attr(st, k, None)
            code = self._normalize_lang_string(v) if v else None
            if code:
                return code
        txt = " ".join(str(self.get_attr(st, k, "") or "") for k in ("displayTitle", "title", "audioChannelLayout", "profile", "selected", "id"))
        if self._ITA_PAT.search(txt):
            return "it"
        if self._ENG_PAT.search(txt):
            return "en"
        return None

    def has_atmos(self, st):
        txt = " ".join(str(self.get_attr(st, k, "") or "") for k in ("audioProfile", "profile", "title", "displayTitle", "audioChannelLayout")).lower()
        return "atmos" in txt or "dolby atmos" in txt

    def has_dtsx(self, st):
        txt = " ".join(str(self.get_attr(st, k, "") or "") for k in ("codec", "profile", "title", "displayTitle")).lower()
        return "dts:x" in txt or "dtsx" in txt or "dts x" in txt

    def normalize_codec_label(self, st):
        c = (self.get_attr(st, "codec", "") or "").lower()
        prof = (self.get_attr(st, "profile", "") or "").lower()
        title = (self.get_attr(st, "title", "") or "").lower()
        if "truehd" in c:
            return "TrueHD"
        if "flac" in c:
            return "FLAC"
        if "pcm" in c or "lpcm" in c:
            return "PCM"
        if "eac3" in c or "e-ac3" in c or "dd+" in c:
            return "Dolby Digital Plus"
        if "ac3" in c or "ac-3" in c:
            return "Dolby Digital"
        if "aac" in c:
            return "AAC"
        if "mp3" in c:
            return "MP3"
        if "opus" in c:
            return "OPUS"
        if "vorbis" in c or "ogg" in c:
            return "VORBIS"
        if "alac" in c:
            return "ALAC"
        if "dts" in c or "dca" in c:
            if "x" in prof or "x" in title or self.has_dtsx(st):
                return "DTS:X"
            if "ma" in prof or "hd ma" in prof or "ma" in title:
                return "DTS-HD MA"
            if "hr" in prof or "hd hr" in prof:
                return "DTS-HD HR"
            return "DTS"
        return c.upper() if c else "AUDIO"

    def channels_label(self, st):
        ch = self.get_int(st, "audioChannelCount", None) or self.get_int(st, "channels", None) or self.get_int(st, "audioChannels", None)
        if not ch:
            layout = (self.get_attr(st, "audioChannelLayout", "") or "").lower()
            if "7.1" in layout:
                ch = 8
            elif "5.1" in layout:
                ch = 6
            elif "2.0" in layout or "stereo" in layout:
                ch = 2
        if not ch:
            return ""
        if ch >= 8:
            return "7.1"
        if ch >= 6:
            return "5.1"
        if ch == 2:
            return "2.0"
        return f"{ch}.0"

    def estimate_audio_bitrate_mbps(self, st):
        codec_raw = (self.get_attr(st, "codec", "") or "").lower()
        profile = (self.get_attr(st, "profile", "") or "").lower()
        title = (self.get_attr(st, "title", "") or "").lower()
        channels = self.get_int(st, "audioChannelCount", None) or self.get_int(st, "channels", None) or 2
        sr = self.get_int(st, "samplingRate", None) or 48000
        bd = self.get_int(st, "bitDepth", None) or 16
        if "truehd" in codec_raw:
            return 4.5
        if "dts" in codec_raw or "dca" in codec_raw:
            if ("hd" in codec_raw) or ("ma" in codec_raw) or ("dts-hd" in codec_raw) or ("dtsma" in codec_raw) or ("ma" in title):
                return 5.5 if channels >= 7 else 4.5
            return 1.5
        if codec_raw in {"eac3", "e-ac3", "dd+"} or "eac3" in codec_raw:
            return 0.96
        if codec_raw == "ac3" or "ac3" in codec_raw:
            return 0.640 if channels >= 6 else 0.384
        if codec_raw in {"pcm", "lpcm"} or "pcm" in codec_raw:
            return (sr * bd * channels) / 1e6
        if "flac" in codec_raw:
            return 0.5 * (sr * bd * channels) / 1e6
        if "alac" in codec_raw:
            return 0.6 * (sr * bd * channels) / 1e6
        if "aac" in codec_raw:
            is_he = ("he" in profile) or ("heaac" in profile) or ("he-aac" in profile) or ("hev" in profile)
            if channels <= 2:
                return 0.160 if is_he else 0.256
            if channels <= 6:
                return 0.256 if is_he else 0.512
            return 0.320 if is_he else 0.640
        if "mp3" in codec_raw:
            return 0.192 if channels <= 2 else 0.320
        if "opus" in codec_raw:
            return 0.128 if channels <= 2 else 0.256
        if "vorbis" in codec_raw or "ogg" in codec_raw:
            return 0.192 if channels <= 2 else 0.320
        return None

    def audio_quality_score(self, st):
        base_map = {
            "TrueHD": 9.0, "DTS:X": 8.9, "DTS-HD MA": 8.6, "DTS-HD HR": 8.0,
            "PCM": 8.2, "FLAC": 8.1, "ALAC": 8.0, "Dolby Digital Plus": 7.4,
            "DTS": 7.0, "Dolby Digital": 6.8, "AAC": 6.0, "MP3": 5.5, "OPUS": 6.2, "VORBIS": 5.9,
        }
        label = self.normalize_codec_label(st)
        base = base_map.get(label, 6.0)
        ch = self.get_int(st, "audioChannelCount", None) or self.get_int(st, "channels", None) or 2
        ch_bonus = min(ch, 8) / 8 * 1.2
        obj_bonus = 0.0
        if label != "DTS:X" and self.has_dtsx(st):
            obj_bonus += 0.8
        if self.has_atmos(st):
            obj_bonus += 1.0
        br = self.stream_bitrate_mbps(st) or self.estimate_audio_bitrate_mbps(st) or 0.0
        br_bonus = min(br, 6.0) / 6.0 * 0.6
        return base + ch_bonus + obj_bonus + br_bonus

    def audio_quality_label(self, st):
        lab = self.normalize_codec_label(st)
        parts = [lab]
        if lab != "DTS:X" and self.has_dtsx(st):
            parts.append("DTS:X")
        if self.has_atmos(st):
            parts.append("Atmos")
        ch = self.channels_label(st)
        if ch:
            parts.append(ch)
        return " ".join(parts)

    def _is_selected_or_default(self, st):
        val = str(self.get_attr(st, "selected", "") or self.get_attr(st, "default", "") or "").lower()
        return val in ("1", "true", "yes")

    def _score_tuple_for_sort(self, st):
        score = self.audio_quality_score(st)
        br = self.stream_bitrate_mbps(st) or self.estimate_audio_bitrate_mbps(st) or 0.0
        ch = self.get_int(st, "audioChannelCount", None) or self.get_int(st, "channels", None) or 0
        obj = 1 if (self.has_atmos(st) or self.has_dtsx(st)) else 0
        return round(score, 4), obj, ch, br

    def pick_best_audio_it_en(self, a_streams, file_path=None, return_streams=False):
        its, ens, unknowns = [], [], []
        for st in a_streams:
            lg = self.stream_lang_code(st)
            (its if lg == "it" else ens if lg == "en" else unknowns).append(st)

        def _best(lst):
            return max(lst, key=self._score_tuple_for_sort) if lst else None

        pick_it = _best(its)
        pick_en = _best(ens)

        if pick_it is None and pick_en is not None and len(unknowns) == 1:
            pick_it = unknowns[0]
            unknowns = []
        if pick_en is None and pick_it is not None and len(unknowns) == 1:
            pick_en = unknowns[0]
            unknowns = []

        fname = str(file_path or "")
        want_it = bool(self._FILE_IT_PAT.search(fname))
        want_en = bool(self._FILE_EN_PAT.search(fname))

        if pick_it is None and pick_en is None and want_it and want_en and len(unknowns) == 2:
            u1, u2 = unknowns
            m_it = self._FILE_IT_PAT.search(fname)
            m_en = self._FILE_EN_PAT.search(fname)
            it_first = (m_it.start() if m_it else 10**9) < (m_en.start() if m_en else 10**9)
            u1_sel = self._is_selected_or_default(u1)
            u2_sel = self._is_selected_or_default(u2)
            if u1_sel ^ u2_sel:
                primary = u1 if u1_sel else u2
                secondary = u2 if primary is u1 else u1
            else:
                primary = max([u1, u2], key=self._score_tuple_for_sort)
                secondary = u2 if primary is u1 else u1
            if it_first:
                pick_it, pick_en = primary, secondary
            else:
                pick_en, pick_it = primary, secondary
            unknowns = []

        if pick_it is None and want_it and unknowns:
            pick_it = _best(unknowns)
            unknowns = [st for st in unknowns if st is not pick_it]
        if pick_en is None and want_en and unknowns:
            pick_en = _best(unknowns)
            unknowns = [st for st in unknowns if st is not pick_en]

        def _pack(st):
            if not st:
                return None, ""
            br = self.stream_bitrate_mbps(st) or self.estimate_audio_bitrate_mbps(st)
            return (round(br, 3) if br else None, self.audio_quality_label(st))

        packed_it = _pack(pick_it)
        packed_en = _pack(pick_en)
        if return_streams:
            return (packed_it, pick_it), (packed_en, pick_en)
        return packed_it, packed_en

    # ---------- Clip / bitrate ----------

    def cap_overhead(self, overhead, tot_mbps):
        if tot_mbps and tot_mbps > 0:
            return min(overhead, 0.8, 0.15 * tot_mbps)
        return min(overhead, 0.8)

    def compute_overhead_mbps(self, container, n_audio, n_subs, tot_mbps_for_cap=None):
        c = (container or "").lower()
        base = self.OVERHEAD_BASE.get(c, 0.15 if c in {"mp4", "m4v"} else 0.20 if c == "mkv" else 0.30)
        overhead = base + self.OVERHEAD_PER_SUBTITLE * max(0, n_subs) + self.OVERHEAD_PER_EXTRA_AUDIO * max(0, n_audio - 1)
        return round(self.cap_overhead(overhead, tot_mbps_for_cap), 3)

    def is_probably_feature_ts(self, item, media, part):
        container = (getattr(part, "container", "") or "").lower()
        if container not in {"mpegts", "m2ts", "m2t", "ts"}:
            return False
        size_gib = (getattr(part, "size", 0) or 0) / (1024**3)
        v_streams = self.get_video_streams(item, part)
        a_streams = self.get_audio_streams(item, part)
        s_streams = self.get_subtitle_streams(item, part)
        h = max([self.get_int(s, "height", 0) for s in v_streams] or [0])
        if size_gib >= 20:
            return True
        if h >= 1080 and (len(a_streams) >= 3 or len(s_streams) >= 5):
            return True
        mbps = self.media_total_mbps_via_xml(item, part)
        return bool(mbps and mbps >= 30)

    def compute_bitrates_and_size(self, item, media, part, duration_ms, xml_info=None, part_match_source="xml_missing", resolution=""):
        size_bytes = int(getattr(part, "size", 0) or 0)
        size_gib = size_bytes / (1024**3) if size_bytes else None
        dur_s = (duration_ms or 0) / 1000.0
        total_mbps_calc = ((size_bytes * 8.0 / 1_000_000.0) / dur_s) if (size_bytes and dur_s > 0) else None
        total_mbps_xml = self.media_total_mbps_via_xml(item, part, xml_info=xml_info) if part_match_source in {"xml_part_id", "xml_file_unique", "xml_file_disambiguated", "xml_basename_unique"} else None
        total_source = "missing"
        rejected_reason = ""
        if total_mbps_calc and total_mbps_xml:
            ratio = total_mbps_xml / max(total_mbps_calc, 1e-9)
            if total_mbps_xml < 0.5 and total_mbps_calc > 2.0:
                tot_mbps, total_source, rejected_reason = total_mbps_calc, "calc_xml_rejected", "xml_too_low_vs_calc"
            elif ratio < 0.25 or ratio > 4.0:
                tot_mbps, total_source, rejected_reason = total_mbps_calc, "calc_xml_rejected", "xml_calc_ratio_out_of_range"
            else:
                tot_mbps, total_source = total_mbps_xml, "xml"
        elif total_mbps_calc:
            tot_mbps, total_source = total_mbps_calc, "calc"
        elif total_mbps_xml:
            tot_mbps, total_source = total_mbps_xml, "xml_no_calc"
        else:
            tot_mbps = None

        v_mbps = None
        sec_video_sum = 0.0
        v_streams = self.get_video_streams(item, part)
        primary = self.select_primary_video_stream(v_streams)
        pv = self.stream_bitrate_mbps(primary)
        if pv and pv > 0:
            v_mbps = pv
        for st in v_streams:
            if st is primary:
                continue
            sv = self.stream_bitrate_mbps(st)
            if not sv or sv <= 0:
                sv = max(0.10 * pv, 2.0) if pv and pv > 0 else 3.0
            sec_video_sum += sv

        a_total_mbps = 0.0
        a_streams = self.get_audio_streams(item, part)
        for st in a_streams:
            am = self.kbps_to_mbps(self.get_attr(st, "bitrate", None)) if self.get_attr(st, "bitrate", None) else None
            if not am or am <= 0:
                am = self.estimate_audio_bitrate_mbps(st)
            if am and am > 0:
                a_total_mbps += am

        video_source = "missing"
        if pv and pv > 0:
            video_source = "stream_xml"
        elif primary and self.parse_required_bandwidths_first_mbps(self.get_attr(primary, "requiredBandwidths", None)):
            video_source = "stream_requiredBandwidths"
        if self.config.fast_mode and (v_mbps is None or v_mbps <= 0) and part_match_source not in {"xml_ambiguous", "xml_missing"}:
            xml_vm = self.fetch_video_bitrate_via_xml(item, part, xml_info=xml_info)
            if xml_vm and xml_vm > 0:
                v_mbps = xml_vm
                video_source = "stream_xml"

        n_subs = len(self.get_subtitle_streams(item, part))
        overhead_mbps_raw = self.compute_overhead_mbps(getattr(part, "container", "") or "", len(a_streams), n_subs, tot_mbps_for_cap=tot_mbps)
        v_est_mbps = None
        if (v_mbps is None or v_mbps <= 0) and (tot_mbps is not None):
            v_est_mbps = max(tot_mbps - (a_total_mbps or 0.0) - (sec_video_sum or 0.0) - overhead_mbps_raw, 0.1)
            video_source = "estimated"
        if v_mbps and tot_mbps and v_mbps > tot_mbps * 1.10:
            v_mbps = None
            video_source = "xml_rejected"
            v_est_mbps = max(tot_mbps - (a_total_mbps or 0.0) - (sec_video_sum or 0.0) - overhead_mbps_raw, 0.1)
        if v_mbps and resolution in {"720p", "1080p", "1440p", "2160p"} and v_mbps < 0.1:
            v_mbps = None
            video_source = "xml_rejected"
            v_est_mbps = max((tot_mbps or 0) - (a_total_mbps or 0.0) - (sec_video_sum or 0.0) - overhead_mbps_raw, 0.1) if tot_mbps else None
        return (
            tot_mbps, total_source, v_mbps, v_est_mbps, a_total_mbps, sec_video_sum,
            size_bytes, size_gib, primary, overhead_mbps_raw, n_subs, len(a_streams), total_mbps_calc, total_mbps_xml, rejected_reason, video_source,
        )

    def add_row_from_part(self, item, media, part, kind):
        if self.cancel_event.is_set():
            with self.mtx:
                self.metrics["jobs_done"] += 1
            return
        hdr = self.detect_hdr_robusto(item, media, part)
        part_info, part_match_source = self.find_part_info_from_bundle(item, part, return_source=True)
        res, resolution_source = self.detect_resolution(item=item, media=media, part=part)
        vcodec = getattr(media, "videoCodec", "") or ""
        container = (getattr(part, "container", "") or "").lower()
        dur_ms = self.robust_duration_ms(item, media, part, xml_info=part_info, part_match_source=part_match_source)

        if self.config.skip_short_clips and container in self.CLIP_CONTAINERS:
            dur_s_now = (dur_ms or 0) / 1000.0
            if dur_s_now < self.config.clip_min_seconds and not self.is_probably_feature_ts(item, media, part):
                with self.mtx:
                    self.metrics["rows_skipped_clip"] += 1
                    self.metrics["jobs_done"] += 1
                return

        (
            tot_mbps, total_source, v_mbps, v_est_mbps, a_mbps, sec_vid_mbps,
            size_bytes, size_gib, primary_stream, overhead_mbps_raw, n_subs, n_audio, total_calc_mbps, total_xml_mbps, total_xml_rej_reason, bitrate_video_source,
        ) = self.compute_bitrates_and_size(item, media, part, dur_ms, xml_info=part_info, part_match_source=part_match_source, resolution=res)

        if self.config.xml_verify_video:
            video_final_tmp = v_mbps if (v_mbps and v_mbps > 0) else v_est_mbps
            predicted_tmp = None
            if video_final_tmp and video_final_tmp > 0:
                predicted_tmp = video_final_tmp + (a_mbps or 0.0) + (sec_vid_mbps or 0.0) + (overhead_mbps_raw or 0.0)
            trigger = False
            if tot_mbps and predicted_tmp:
                if abs(predicted_tmp - tot_mbps) / max(tot_mbps, 0.1) > self.config.video_verify_tol:
                    trigger = True
            if container in ("mpegts", "m2ts", "m2t", "ts"):
                trigger = True
            if (sec_vid_mbps or 0.0) > 0.1:
                trigger = True
            if trigger:
                xml_vm2 = self.fetch_video_bitrate_via_xml(item, part)
                if xml_vm2 and xml_vm2 > 0:
                    v_mbps = xml_vm2
                    v_est_mbps = None

        show_meta = self.get_show_meta_for_episode(item) if kind == "TV" else None
        if self.config.run_preset == "FAST_PRECISE":
            imdb_id, imdb_rating, tmdb_id = self.get_ids_rating_from_xml(item)
        else:
            imdb_id, imdb_rating, tmdb_id = self.get_ids_and_rating(item)
        if kind == "TV" and show_meta:
            imdb_id = imdb_id or show_meta.get("imdb_id")
            imdb_rating = imdb_rating if imdb_rating is not None else show_meta.get("imdb_rating")
            tmdb_id = tmdb_id or show_meta.get("tmdb_id")
        if kind == "TV":
            gens = (show_meta.get("genres") if show_meta else "") or ""
        else:
            gens = self.get_genres_from_xml(item) or self.get_genres_from_entity(item) or ""

        year = getattr(item, "year", None)
        added_raw = getattr(item, "addedAt", None)
        added_at_milan = None
        if isinstance(added_raw, datetime):
            if added_raw.tzinfo is None:
                added_raw = added_raw.replace(tzinfo=timezone.utc)
            added_at_milan = added_raw.astimezone(TZ_MILAN)
        added_at_str = added_at_milan.strftime("%Y-%m-%d %H:%M:%S") if added_at_milan else None

        a_streams = self.get_audio_streams(item, part)
        file_path = getattr(part, "file", "") or ""
        (it_br, it_lab), (en_br, en_lab) = self.pick_best_audio_it_en(a_streams, file_path=file_path)

        video_final_raw = v_mbps if (v_mbps is not None and v_mbps > 0) else v_est_mbps
        video_final_budget = video_final_raw
        overhead_budget = 0.0 if total_source == "xml" else (overhead_mbps_raw or 0.0)
        v_used = video_final_budget or 0.0
        a_used = a_mbps or 0.0
        s_used = sec_vid_mbps or 0.0
        if tot_mbps and tot_mbps > 0:
            leftover = tot_mbps - v_used - overhead_budget - a_used - s_used
            if leftover < -1e-9:
                adjustable = max(a_used, 0.0) + max(s_used, 0.0)
                if adjustable > 0:
                    scale = max(0.0, (tot_mbps - v_used - overhead_budget)) / adjustable
                    scale = min(1.0, scale)
                    a_used = round(a_used * scale, 6)
                    s_used = round(s_used * scale, 6)
                else:
                    if overhead_budget > 0:
                        overhead_budget = max(0.0, tot_mbps - v_used)
                    elif v_est_mbps is not None and (v_mbps is None or v_mbps <= 0):
                        v_used = max(0.0, tot_mbps - overhead_budget)
                        video_final_budget = v_used
                        a_used = 0.0
                        s_used = 0.0
                    else:
                        a_used = 0.0
                        s_used = 0.0

        if self.config.output_profile == "SLIM_RAW":
            video_out = video_final_raw
            audio_out = a_mbps
            secondary_out = sec_vid_mbps
            overhead_out = overhead_mbps_raw
        else:
            video_out = video_final_budget
            audio_out = a_used
            secondary_out = s_used
            overhead_out = overhead_budget

        dur_s = (dur_ms or 0) / 1000.0 if dur_ms else None
        dur_hms = self.format_duration_hms(dur_s) if dur_s is not None else None

        full_row = {
            "type": kind,
            "rating_key": getattr(item, "ratingKey", None),
            "title_or_series": getattr(item, "title", "") if kind == "Movie" else self.get_series_title_for_episode(item),
            "season": None if kind == "Movie" else getattr(item, "parentIndex", None),
            "episode": None if kind == "Movie" else getattr(item, "index", None),
            "episode_title": "" if kind == "Movie" else getattr(item, "title", ""),
            "year": year,
            "added_at_milan": added_at_str,
            "resolution": res,
            "hdr": hdr,
            "videoCodec": vcodec,
            "container": container,
            "bitrate_mbps_total": round(tot_mbps, 3) if tot_mbps is not None else None,
            "bitrate_total_source": total_source,
            "bitrate_mbps_video": round(video_out, 3) if video_out is not None else None,
            "bitrate_mbps_video_est": round(v_est_mbps, 3) if v_est_mbps is not None else None,
            "bitrate_mbps_video_final": round(video_final_budget, 3) if video_final_budget is not None else None,
            "audio_bitrate_total_mbps_raw": round(a_mbps, 3) if a_mbps is not None else None,
            "secondary_video_mbps_raw": round(sec_vid_mbps, 3) if sec_vid_mbps is not None else None,
            "container_overhead_mbps_raw": round(overhead_mbps_raw, 3) if overhead_mbps_raw is not None else None,
            "audio_bitrate_total_mbps": round(audio_out, 3) if audio_out is not None else None,
            "secondary_video_mbps": round(secondary_out, 3) if secondary_out is not None else None,
            "container_overhead_mbps": round(overhead_out, 3) if overhead_out is not None else None,
            "size_gib": round(size_gib, 3) if size_gib is not None else None,
            "imdb_id": imdb_id,
            "imdb_rating": imdb_rating,
            "tmdb_id": tmdb_id,
            "genres": gens,
            "duration_s": round(dur_s, 3) if dur_s is not None else None,
            "duration_hms": dur_hms,
            "file": file_path,
            "audio_it_bitrate_mbps": it_br,
            "audio_it_quality": it_lab or "",
            "audio_en_bitrate_mbps": en_br,
            "audio_en_quality": en_lab or "",
            "media_id": getattr(media, "id", None),
            "part_id": getattr(part, "id", None),
            "part_match_source": part_match_source,
            "resolution_source": resolution_source,
            "duration_source": self.duration_source_cache.get(getattr(part, "file", None), "missing"),
            "bitrate_total_calc_mbps": round(total_calc_mbps, 3) if total_calc_mbps is not None else None,
            "bitrate_total_xml_mbps": round(total_xml_mbps, 3) if total_xml_mbps is not None else None,
            "bitrate_total_xml_rejected_reason": total_xml_rej_reason or "",
            "bitrate_video_source": bitrate_video_source,
        }
        row = {k: full_row.get(k, None) for k in self.output_columns}
        with self.mtx:
            self.rows.append(row)
            self.metrics["rows_created"] += 1
            self.metrics["jobs_done"] += 1

        if self.config.debug:
            self._append_debug_rows(item, media, part, kind, full_row, primary_stream, a_streams, n_audio, n_subs, total_source)

    def _append_debug_rows(self, item, media, part, kind, full_row, primary_stream, a_streams, n_audio, n_subs, total_source):
        file_path = getattr(part, "file", "") or ""
        try:
            all_streams = self.safe_streams(item, part) or []
        except Exception:
            all_streams = []
        item_dict = self.obj_attribs_dict(item)
        media_dict = self.obj_attribs_dict(media)
        part_dict = self.obj_attribs_dict(part)
        video_dicts = [self.stream_attribs_dict(st) for st in all_streams if getattr(st, "streamType", None) == 1]
        audio_dicts = [self.stream_attribs_dict(st) for st in all_streams if getattr(st, "streamType", None) == 2]
        sub_dicts = [self.stream_attribs_dict(st) for st in all_streams if getattr(st, "streamType", None) == 3]
        other_dicts = [self.stream_attribs_dict(st) for st in all_streams if getattr(st, "streamType", None) not in (1, 2, 3)]
        try:
            (it_pack, it_pick), (en_pack, en_pick) = self.pick_best_audio_it_en(a_streams, file_path=file_path, return_streams=True)
        except Exception:
            it_pick = en_pick = None
        try:
            t_xml = self.media_total_mbps_via_xml(item, part)
        except Exception:
            t_xml = None
        try:
            v_xml = self.fetch_video_bitrate_via_xml(item, part)
        except Exception:
            v_xml = None
        color_trc = self.get_attr(primary_stream, "colorTrc", "") if primary_stream is not None else ""
        dbg_row = {
            "type": kind,
            "rating_key": getattr(item, "ratingKey", None),
            "title_or_series": full_row.get("title_or_series"),
            "season": full_row.get("season"),
            "episode": full_row.get("episode"),
            "episode_title": full_row.get("episode_title"),
            "year": full_row.get("year"),
            "added_at_milan": full_row.get("added_at_milan"),
            "resolution": full_row.get("resolution"),
            "hdr": full_row.get("hdr"),
            "videoCodec": full_row.get("videoCodec"),
            "container": full_row.get("container"),
            "file": file_path,
            "duration_s": full_row.get("duration_s"),
            "duration_hms": full_row.get("duration_hms"),
            "dbg_duration_source": self.duration_source_cache.get(getattr(part, "file", None), "unknown"),
            "part_match_source": full_row.get("part_match_source"),
            "resolution_source": full_row.get("resolution_source"),
            "duration_source": full_row.get("duration_source"),
            "bitrate_mbps_total": full_row.get("bitrate_mbps_total"),
            "dbg_total_source": total_source,
            "bitrate_total_source": full_row.get("bitrate_total_source"),
            "bitrate_total_calc_mbps": full_row.get("bitrate_total_calc_mbps"),
            "bitrate_total_xml_mbps": full_row.get("bitrate_total_xml_mbps"),
            "bitrate_total_xml_rejected_reason": full_row.get("bitrate_total_xml_rejected_reason"),
            "bitrate_video_source": full_row.get("bitrate_video_source"),
            "bitrate_mbps_video_out": full_row.get("bitrate_mbps_video"),
            "dbg_media_bitrate_mbps_xml": round(t_xml, 3) if t_xml else None,
            "dbg_video_bitrate_mbps_xml": round(v_xml, 3) if v_xml else None,
            "dbg_video_colorTrc": color_trc,
            "dbg_num_audio": n_audio,
            "dbg_num_subs": n_subs,
            "dbg_audio_it": full_row.get("audio_it_quality"),
            "dbg_audio_en": full_row.get("audio_en_quality"),
            "dbg_tv_show_fallback_level": "show" if kind == "TV" else "",
            "dbg_item_attribs_json": self.clip_excel_cell(self.json_dumps_safe(item_dict)),
            "dbg_media_attribs_json": self.clip_excel_cell(self.json_dumps_safe(media_dict)),
            "dbg_part_attribs_json": self.clip_excel_cell(self.json_dumps_safe(part_dict)),
            "dbg_video_streams_all_json": self.clip_excel_cell(self.json_dumps_safe(video_dicts)),
            "dbg_audio_streams_all_json": self.clip_excel_cell(self.json_dumps_safe(audio_dicts)),
            "dbg_subtitle_streams_all_json": self.clip_excel_cell(self.json_dumps_safe(sub_dicts)),
            "dbg_other_streams_all_json": self.clip_excel_cell(self.json_dumps_safe(other_dicts)),
            "dbg_audio_pick_it_json": self.clip_excel_cell(self.json_dumps_safe(self.stream_attribs_dict(it_pick))) if it_pick else "",
            "dbg_audio_pick_en_json": self.clip_excel_cell(self.json_dumps_safe(self.stream_attribs_dict(en_pick))) if en_pick else "",
        }
        with self.mtx:
            self.debug_rows.append(dbg_row)
            type_counts = {}
            for pos_all, st in enumerate(all_streams, 1):
                d = self.stream_attribs_dict(st)
                try:
                    stype = int(d.get("streamType", getattr(st, "streamType", None)))
                except Exception:
                    stype = None
                label = {1: "video", 2: "audio", 3: "subtitle"}.get(stype, "other")
                type_counts[label] = type_counts.get(label, 0) + 1
                sr = {
                    "type": kind,
                    "rating_key": getattr(item, "ratingKey", None),
                    "title_or_series": full_row.get("title_or_series"),
                    "season": full_row.get("season"),
                    "episode": full_row.get("episode"),
                    "episode_title": full_row.get("episode_title"),
                    "file": file_path,
                    "stream_pos_all": pos_all,
                    "stream_pos_type": type_counts[label],
                    "streamType": stype,
                    "streamTypeLabel": label,
                    "stream_attribs_json": self.clip_excel_cell(self.json_dumps_safe(d)),
                }
                for k, v in d.items():
                    sr[f"st_{k}"] = v
                self.debug_stream_rows.append(sr)

    def _save_outputs(self, output_dir: pathlib.Path) -> tuple[Optional[str], Optional[str]]:
        df = pd.DataFrame(self.rows)
        if not df.empty:
            sort_cols = ["type", "title_or_series"]
            if "season" in df.columns:
                sort_cols.append("season")
            if "episode" in df.columns:
                sort_cols.append("episode")
            if "file" in df.columns:
                sort_cols.append("file")
            df = df.sort_values(by=sort_cols, kind="stable")
            num_cols = [
                "year", "season", "episode", "size_gib", "bitrate_mbps_total", "bitrate_mbps_video",
                "audio_bitrate_total_mbps", "secondary_video_mbps", "container_overhead_mbps", "imdb_rating",
                "audio_it_bitrate_mbps", "audio_en_bitrate_mbps",
            ]
            if self.config.duration_output == "BOTH" and "duration_s" in df.columns:
                num_cols.append("duration_s")
            for c in num_cols:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce").round(3)
        else:
            df = pd.DataFrame(columns=self.output_columns)

        base = self.config.output_basename or "plex_inventory_fast_slim"
        csv_path = str(output_dir / with_timestamp(f"{base}.csv")) if self.config.write_csv else None
        xlsx_path = str(output_dir / with_timestamp(f"{base}.xlsx")) if self.config.write_xlsx else None
        if csv_path:
            df.to_csv(csv_path, index=False)
        if xlsx_path:
            with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Library")
                if self.config.debug and self.debug_rows:
                    pd.DataFrame(self.debug_rows).to_excel(writer, index=False, sheet_name="Debug_XML")
                if self.config.debug and self.debug_stream_rows:
                    pd.DataFrame(self.debug_stream_rows).to_excel(writer, index=False, sheet_name="Debug_Streams")
        return csv_path, xlsx_path

    def _format_job_error(self, job, exc) -> str:
        try:
            item, media, part, kind = job or (None, None, None, "?")
            ep_title = (getattr(item, "title", "") or "") if item else ""
            series = self.get_series_title_for_episode(item) if (kind == "TV" and item is not None) else ""
            title = f"{series} - {ep_title}" if (series and ep_title) else (series or ep_title or "")
            fpath = (getattr(part, "file", "") or "") if part else ""
            base = pathlib.Path(fpath).name if fpath else ""
            return f"{kind} | {title} | {base} | {exc!r}"
        except Exception:
            return repr(exc)

    def _log_final_report(self, elapsed, csv_path, xlsx_path):
        cfg = self.config
        http_concurrency = cfg.http_concurrency_fast if cfg.run_preset == "FAST_PRECISE" else cfg.http_concurrency_slow
        self.log("")
        self.log(
            f"OK. RUN_PRESET={cfg.run_preset} OUTPUT_PROFILE={cfg.output_profile} FAST_MODE={cfg.fast_mode} "
            f"HDR_XML_ON_FAST={cfg.hdr_xml_on_fast} XML_VERIFY_VIDEO={cfg.xml_verify_video} "
            f"MAX_WORKERS={cfg.max_workers} HTTP_CONCURRENCY={http_concurrency}"
        )
        if csv_path:
            self.log(f"Creato CSV:  {csv_path}")
        if xlsx_path:
            self.log(f"Creato XLSX: {xlsx_path}")
        self.log("Stats:")
        self.log(f"- parts totali (jobs): {self.metrics['jobs_total']}")
        self.log(f"- parts completati (ok+skipped): {self.metrics['jobs_done']} | errori: {self.metrics['errors_total']}")
        self.log(f"- righe create: {self.metrics['rows_created']} | clip ts/m2ts saltati (<{cfg.clip_min_seconds}s): {self.metrics['rows_skipped_clip']}")
        expected_rows = max(self.metrics["jobs_total"] - self.metrics["rows_skipped_clip"] - self.metrics["errors_total"], 0)
        self.log(f"- righe attese: {expected_rows}")
        if self.metrics["rows_created"] != expected_rows:
            self.log(f"[WARN] righe create ({self.metrics['rows_created']}) != attese ({expected_rows}).")
        self.log(f"- tempo: {elapsed:.1f}s (~{elapsed / max(self.metrics['jobs_total'], 1):.3f}s per part)")
        self.log(f"- cache hits: streams={self.metrics['streams_cache_hit']} duration={self.metrics['duration_cache_hit']}")
        self.log(f"- XML fetch: {self.metrics['xml_fetch']} (cache hits: {self.metrics['xml_cache_hit']})")


def run_inventory(
    config: InventoryConfig,
    progress_callback: Optional[ProgressCallback] = None,
    log_callback: Optional[LogCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> InventoryResult:
    return InventoryRunner(config, progress_callback, log_callback, cancel_event).run()
