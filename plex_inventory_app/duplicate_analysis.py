from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
import re
import pandas as pd

from .duplicate_policy_v12 import (
    POLICY_VERSION, audio_better, audio_score, basename, hdr_rank, lowbit4k_penalty,
    normalize_text, normalized_basename, parse_audio_quality, resolution_rank, source_tag_from_path, SOURCE_RANK,
)
from .duplicate_report_writer import write_duplicate_report

REQUIRED_COLUMNS = ["type","title_or_series","season","episode","episode_title","year","resolution","hdr","videoCodec","container","duration_hms","bitrate_mbps_video","audio_it_bitrate_mbps","audio_it_quality","audio_en_bitrate_mbps","audio_en_quality","size_gib","imdb_id","rating_key","file"]


@dataclass
class InventoryWorkbook:
    library: pd.DataFrame
    debug_xml: pd.DataFrame | None
    debug_streams: pd.DataFrame | None
    warnings: list[str]


def _parse_duration_seconds(value: str) -> int:
    if not isinstance(value, str) or ":" not in value:
        return 0
    p = [int(x) for x in value.split(":")]
    return p[0] * 3600 + p[1] * 60 + p[2] if len(p) == 3 else 0


def load_inventory_workbook(path: Path, log_callback: Callable[[str], None] | None = None) -> InventoryWorkbook:
    log = log_callback or (lambda _msg: None)
    log(f"Apro workbook: {path}")
    warnings: list[str] = []
    with pd.ExcelFile(path) as workbook:
        sheet_names = list(workbook.sheet_names)
        log(f"Fogli trovati: {', '.join(sheet_names)}")
        if "Library" not in sheet_names:
            raise ValueError("Workbook inventario non valido: manca il foglio 'Library'.")
        log("Lettura foglio Library...")
        library = workbook.parse("Library").copy()
        debug_xml = None
        debug_streams = None
        if "Debug_XML" in sheet_names:
            log("Lettura Debug_XML...")
            debug_xml = workbook.parse("Debug_XML")
        if "Debug_Streams" in sheet_names:
            log("Lettura Debug_Streams...")
            debug_streams = workbook.parse("Debug_Streams")
    missing = [c for c in REQUIRED_COLUMNS if c not in library.columns]
    if missing:
        raise ValueError(f"Colonne obbligatorie mancanti nel foglio Library: {', '.join(missing)}")
    if debug_streams is None:
        warnings.append("Debug_Streams non presente: uso fallback Library/Debug_XML")
    if debug_xml is None:
        warnings.append("Debug_XML non presente: uso fallback Library")
    log("Workbook letto correttamente")
    return InventoryWorkbook(library=library, debug_xml=debug_xml, debug_streams=debug_streams, warnings=warnings)


def movie_group_key(row: pd.Series) -> str:
    if str(row.get("imdb_id") or "").strip():
        return f"movie:imdb:{row['imdb_id']}"
    if str(row.get("tmdb_id") or "").strip():
        return f"movie:tmdb:{row['tmdb_id']}"
    return f"movie:titleyear:{normalize_text(row.get('title_or_series'))}:{str(row.get('year') or '').strip()}"


def tv_group_key(row: pd.Series) -> str:
    title = normalize_text(row.get("title_or_series"))
    season = str(row.get("season") or "")
    episode = str(row.get("episode") or "")
    year = str(row.get("year") or "").strip()
    return f"tv:{title}:{year}:s{season}:e{episode}" if year else f"tv:{title}:s{season}:e{episode}"


def build_group_key(row: pd.Series) -> str:
    return movie_group_key(row) if str(row.get("type", "")).lower() == "movie" else tv_group_key(row)


def detect_italian_audio_state(row: pd.Series, ds: pd.DataFrame | None, dx: pd.DataFrame | None) -> str:
    rk = str(row.get("rating_key") or "")
    file_path = str(row.get("file") or "")
    def _scan(df: pd.DataFrame | None) -> str | None:
        if df is None:
            return None
        cols = {c.lower(): c for c in df.columns}
        matches = df
        if "rating_key" in cols:
            matches = df[df[cols["rating_key"]].astype(str) == rk]
        if matches.empty:
            return None
        text = " ".join(matches.astype(str).fillna("").agg(" ".join, axis=1).tolist()).lower()
        tokens = set(re.findall(r"[a-z]+", text))
        positive_tokens = {"italian", "italiano", "ita", "it"}
        negative_tokens = {"latino", "latin", "american", "spanish", "espanol", "spa", "castilian"}
        if tokens & positive_tokens and not (tokens & negative_tokens):
            return "yes"
        if any(x in text for x in ["audio", "language", "lang"]):
            return "no"
        return None
    for src in (_scan(ds), _scan(dx)):
        if src:
            return src
    q = str(row.get("audio_it_quality") or "").strip().lower()
    if q:
        return "yes"
    if source_tag_from_path(file_path, str(row.get("container") or "")) == "full_disc":
        return "unknown"
    return "unknown"


def _duration_cluster_movie(group: pd.DataFrame) -> pd.Series:
    d = group["duration_seconds"].fillna(0).astype(int).sort_values()
    cluster = {}
    idx = 0
    prev = None
    for i, sec in d.items():
        if prev is not None and abs(sec - prev) >= 60:
            idx += 1
        cluster[i] = idx
        prev = sec
    return pd.Series(cluster)


def analyze_duplicates(
    inventory_path: Path,
    output_dir: Path,
    log_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> Path:
    log = log_callback or (lambda _msg: None)
    progress = progress_callback or (lambda _done, _total, _msg: None)
    done_units = 0
    total_units = 0
    wb = load_inventory_workbook(inventory_path, log_callback=log)
    log("Workbook caricato")
    for w in wb.warnings:
        log(f"WARNING: {w}")
    df = wb.library.copy()
    log(f"Righe lette: {len(df)}")
    total_rows = len(df)
    total_units = 6 + total_rows + 1 + 1 + 2
    done_units += 1
    progress(done_units, total_units, "Workbook letto")
    progress(done_units, total_units, "Preparazione analisi duplicati")

    log("Normalizzazione titoli e percorsi...")
    done_units += 1
    progress(done_units, total_units, "Normalizzazione titoli e percorsi")
    df["normalized_title"] = df["title_or_series"].map(normalize_text)
    df["normalized_basename"] = df["file"].map(normalized_basename)
    log("Calcolo durate...")
    done_units += 1
    progress(done_units, total_units, "Calcolo durate")
    df["duration_seconds"] = df["duration_hms"].map(_parse_duration_seconds)
    log("Calcolo ranking video e sorgente...")
    done_units += 1
    progress(done_units, total_units, "Calcolo ranking video e sorgente")
    df["resolution_rank"] = df["resolution"].map(resolution_rank)
    df["hdr_rank"] = df["hdr"].map(hdr_rank)
    df["source_tag"] = df.apply(lambda r: source_tag_from_path(str(r.get("file") or ""), str(r.get("container") or "")), axis=1)
    df["source_rank"] = df["source_tag"].map(lambda s: SOURCE_RANK.get(s, SOURCE_RANK["encode"]))
    log("Analisi audio italiano da Debug_Streams/Debug_XML...")
    states = []
    for i, (_, row) in enumerate(df.iterrows(), start=1):
        states.append(detect_italian_audio_state(row, wb.debug_streams, wb.debug_xml))
        done_units += 1
        if i % 250 == 0 or i == total_rows:
            log(f"Analisi audio italiano: {i}/{total_rows} righe")
            progress(done_units, total_units, "Analisi audio italiano")
    df["italian_audio_state"] = states
    log("Calcolo punteggi audio...")
    done_units += 1
    progress(done_units, total_units, "Calcolo punteggi audio")
    df["audio_it_score"] = df.apply(lambda r: audio_score(parse_audio_quality(r.get("audio_it_quality"), r.get("audio_it_bitrate_mbps"))), axis=1)
    df["audio_en_score"] = df.apply(lambda r: audio_score(parse_audio_quality(r.get("audio_en_quality"), r.get("audio_en_bitrate_mbps"))), axis=1)
    log("Creazione gruppi duplicati...")
    done_units += 1
    df["group_key"] = df.apply(build_group_key, axis=1)
    df["cluster_index"] = 0
    movie_groups = [group for _, group in df.groupby("group_key") if str(group.iloc[0].get("type", "")).lower() == "movie"]
    total_units = 6 + total_rows + max(len(movie_groups), 1) + 1 + 2
    done_units = min(done_units, total_units)
    progress(done_units, total_units, "Creazione gruppi duplicati")

    log("Split gruppi film per durata...")
    total_movie_groups = len(movie_groups)
    for done, group in enumerate(movie_groups, start=1):
        done_units = min(done_units + 1, total_units)
        if str(group.iloc[0].get("type", "")).lower() == "movie":
            c = _duration_cluster_movie(group)
            for row_idx, cluster_idx in c.items():
                df.loc[row_idx, "cluster_index"] = cluster_idx
        if done % 25 == 0 or done == total_movie_groups:
            log(f"Split durata film: {done}/{total_movie_groups} gruppi")
            progress(done_units, total_units, "Split gruppi film per durata")
    if total_movie_groups == 0:
        done_units = min(done_units + 1, total_units)
        progress(done_units, total_units, "Split gruppi film per durata")
    rows = []
    dup_groups = 0
    log("Classificazione gruppi duplicati...")
    clustered = list(df.groupby(["group_key", "cluster_index"]))
    total_clusters = len(clustered)
    total_units = 6 + total_rows + max(len(movie_groups), 1) + max(total_clusters, 1) + 2
    done_units = min(done_units, total_units)
    for processed, ((_, _), cluster) in enumerate(clustered, start=1):
        done_units = min(done_units + 1, total_units)
        if processed % 25 == 0 or processed == total_clusters:
            log(f"Classificazione gruppi: {processed}/{total_clusters}")
            progress(done_units, total_units, "Classificazione gruppi duplicati")
        if len(cluster) < 2:
            continue
        dup_groups += 1
        has_good_1080 = ((cluster["resolution_rank"] == 3) & (cluster["bitrate_mbps_video"].fillna(0) > 1.5)).any()
        cluster = cluster.copy()
        cluster["lowbit4k_penalized"] = cluster.apply(lambda r: lowbit4k_penalty(str(r.get("type", "")).lower()=="movie", int(r["resolution_rank"]), float(r.get("bitrate_mbps_video") or 0), bool(has_good_1080)), axis=1)
        ordered = cluster.sort_values(by=["lowbit4k_penalized","bitrate_mbps_video","resolution_rank","hdr_rank","audio_it_score","source_rank","audio_en_score","size_gib","normalized_basename"], ascending=[True,False,False,False,False,False,False,False,True])
        keeper = ordered.iloc[0]
        special_mask = cluster["source_tag"].isin(["full_disc","dirtyhippie","ai_upscale"])
        for _, row in cluster.iterrows():
            action = "KEEP" if row.name == keeper.name or bool(special_mask.loc[row.name]) else "DELETE_SAFE"
            reason = ["versione tenuta con le regole attuali"] if action == "KEEP" else ["differenze contenute: copia ridondante"]
            if action != "KEEP":
                if float(row.get("bitrate_mbps_video") or 0) < float(keeper.get("bitrate_mbps_video") or 0):
                    reason.append(f"bitrate video inferiore ({float(row.get('bitrate_mbps_video') or 0):.2f} < {float(keeper.get('bitrate_mbps_video') or 0):.2f} Mbps)")
                if int(row.get("resolution_rank") or 0) < int(keeper.get("resolution_rank") or 0):
                    reason.append(f"risoluzione inferiore: {row.get('resolution')} vs {keeper.get('resolution')}")
                if int(row.get("hdr_rank") or 0) < int(keeper.get("hdr_rank") or 0):
                    reason.append(f"HDR inferiore: {row.get('hdr')} vs {keeper.get('hdr')}")
                if row.get("source_tag") != keeper.get("source_tag"):
                    reason.append(f"sorgente diversa ({row.get('source_tag')} vs {keeper.get('source_tag')})")
                row_it = parse_audio_quality(row.get("audio_it_quality"), row.get("audio_it_bitrate_mbps"))
                keep_it = parse_audio_quality(keeper.get("audio_it_quality"), keeper.get("audio_it_bitrate_mbps"))
                if audio_better(row_it, keep_it, "it") and abs(float(row.get("bitrate_mbps_video") or 0) - float(keeper.get("bitrate_mbps_video") or 0)) < 0.8:
                    action = "REVIEW_MANUAL"
                    reason = ["vantaggi incrociati: video vs audio/sorgente", "audio IT migliore sul file da valutare (...)"]
                row_en = parse_audio_quality(row.get("audio_en_quality"), row.get("audio_en_bitrate_mbps"))
                keep_en = parse_audio_quality(keeper.get("audio_en_quality"), keeper.get("audio_en_bitrate_mbps"))
                if action == "DELETE_SAFE" and audio_better(row_en, keep_en, "en") and not audio_better(row_it, keep_it, "it"):
                    action = "DELETE_PROPOSED"
                    reason = ["vantaggio residuo audio EN sul file da valutare", "resta un vantaggio secondario audio EN"]
            rows.append({**row.to_dict(), "title_or_episode": row.get("episode_title") or row.get("title_or_series"), "file_path": row.get("file"), "keep_reference": keeper.get("file"), "final_action": action, "reason": " ; ".join(reason)})
    if total_clusters == 0:
        done_units += 1
        progress(done_units, total_units, "Classificazione gruppi duplicati")
    log(f"Gruppi duplicati trovati: {dup_groups}")
    out_df = pd.DataFrame(rows)
    if out_df.empty:
        out_df = pd.DataFrame(columns=[
            "group_key","cluster_index","group_status","title_or_episode","type","resolution","hdr","bitrate_mbps_video",
            "audio_it_quality","audio_it_bitrate_mbps","audio_en_quality","audio_en_bitrate_mbps","source_tag","italian_audio_state",
            "final_action","reason","normalized_basename","file_path","keep_reference","rating_key"
        ])
    if not out_df.empty:
        out_df["group_status"] = out_df.groupby(["group_key", "cluster_index"])["final_action"].transform(lambda s: "MANUALE" if (s=="REVIEW_MANUAL").any() else ("CONSERVA" if (s=="KEEP").sum()>1 else "AUTO_GROUP"))
    if dup_groups == 0:
        log("Nessun gruppo duplicato trovato nel report selezionato.")
    keep_count = int((out_df.final_action == "KEEP").sum()) if not out_df.empty else 0
    delete_safe_count = int((out_df.final_action == "DELETE_SAFE").sum()) if not out_df.empty else 0
    delete_proposed_count = int((out_df.final_action == "DELETE_PROPOSED").sum()) if not out_df.empty else 0
    manual_count = int((out_df.final_action == "REVIEW_MANUAL").sum()) if not out_df.empty else 0
    log(
        "Conteggi finali - "
        f"KEEP: {keep_count}, DELETE_SAFE: {delete_safe_count}, "
        f"DELETE_PROPOSED: {delete_proposed_count}, REVIEW_MANUAL: {manual_count}"
    )
    summary = pd.DataFrame({"metrica":["policy_version","policy_coverage_note","inventory_file","generated_at","total_rows","duplicate_groups","keep_count","delete_safe_count","delete_proposed_count","manual_count","conserva_count","debug_streams_used","debug_xml_used"],"valore":[POLICY_VERSION,"prima integrazione: alcune regole avanzate ancora parziali",str(inventory_path),datetime.now().isoformat(timespec="seconds"),len(df),dup_groups,keep_count,delete_safe_count,delete_proposed_count,manual_count,keep_count,wb.debug_streams is not None,wb.debug_xml is not None]})
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"report_duplicati_plex_classificato_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    log("Scrittura workbook finale...")
    done_units = min(done_units + 1, total_units)
    progress(done_units, total_units, "Scrittura workbook finale")
    write_duplicate_report(out_path, summary, out_df)
    log(f"Report scritto: {out_path}")
    progress(total_units, total_units, "Report duplicati completato")
    return out_path
