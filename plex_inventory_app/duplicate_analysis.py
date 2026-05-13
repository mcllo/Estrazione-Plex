from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
import re
import math
import pandas as pd

from .duplicate_policy_v12 import (
    POLICY_VERSION, audio_better, audio_score, basename, hdr_rank, lowbit4k_penalty,
    normalize_text, normalized_basename, parse_audio_quality, resolution_rank, source_tag_from_path, SOURCE_RANK,
    candidate_score, candidate_sort_key,
)
from .duplicate_report_writer import write_duplicate_report


class ReasonBuilder:

    @staticmethod
    def _clean_text(value: object) -> str:
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass
        text = str(value).strip()
        return "" if text.lower() in {"nan", "none", "null"} else text
    @staticmethod
    def _fmt_video_bitrate(value: object) -> str:
        try:
            parsed = float(value)
            if math.isnan(parsed):
                return "n/d"
            return f"{parsed:.3f} Mbps"
        except (TypeError, ValueError):
            return "n/d"

    @staticmethod
    def _fmt_audio_kbps(value: object) -> str:
        try:
            parsed = float(value)
            if math.isnan(parsed):
                return "n/d"
            return f"{int(round(parsed * 1000.0))} kbps"
        except (TypeError, ValueError):
            return "n/d"

    @staticmethod
    def format_video_label(row: pd.Series) -> str:
        codec = ReasonBuilder._clean_text(row.get("videoCodec"))
        res = ReasonBuilder._clean_text(row.get("resolution"))
        hdr = ReasonBuilder._clean_text(row.get("hdr")) or "SDR"
        bitrate = ReasonBuilder._fmt_video_bitrate(row.get("bitrate_mbps_video"))
        parts = [x for x in [codec, res, hdr, bitrate] if x]
        return " ".join(parts)

    @staticmethod
    def format_audio_it_label(row: pd.Series) -> str:
        quality = ReasonBuilder._clean_text(row.get("audio_it_quality"))
        bitrate = ReasonBuilder._fmt_audio_kbps(row.get("audio_it_bitrate_mbps"))
        return " ".join([x for x in [quality, bitrate] if x]).strip()

    @staticmethod
    def format_audio_en_label(row: pd.Series) -> str:
        quality = ReasonBuilder._clean_text(row.get("audio_en_quality"))
        bitrate = ReasonBuilder._fmt_audio_kbps(row.get("audio_en_bitrate_mbps"))
        return " ".join([x for x in [quality, bitrate] if x]).strip()

    @staticmethod
    def build_keep_reason() -> str:
        return "versione tenuta con le regole attuali"

    @staticmethod
    def build_conserva_reason() -> str:
        return "durata diversa del film: tengo una versione per ciascun taglio"

REQUIRED_COLUMNS = ["type","title_or_series","season","episode","episode_title","year","resolution","hdr","videoCodec","container","duration_hms","bitrate_mbps_video","audio_it_bitrate_mbps","audio_it_quality","audio_en_bitrate_mbps","audio_en_quality","size_gib","imdb_id","rating_key","file"]


MISSING_TOKENS = {"", "nan", "null", "none", "-", "—", "n/a", "na"}

def _is_missing_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    text = str(value).strip().lower()
    return text in MISSING_TOKENS

def _normalized_year(value: object) -> str:
    if _is_missing_value(value):
        return ""
    try:
        num = float(value)
        if pd.isna(num):
            return ""
        if num.is_integer():
            return str(int(num))
    except (TypeError, ValueError):
        pass
    return str(value).strip()


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
    year = _normalized_year(row.get("year"))
    imdb = "" if _is_missing_value(row.get("imdb_id")) else str(row.get("imdb_id")).strip()
    tmdb = "" if _is_missing_value(row.get("tmdb_id")) else str(row.get("tmdb_id")).strip()
    if imdb:
        return f"movie:imdb:{imdb}"
    if tmdb:
        return f"movie:tmdb:{tmdb}"
    return f"movie:titleyear:{normalize_text(row.get('title_or_series'))}:{year}"


def _episode_component(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    try:
        num = float(text)
        if pd.isna(num):
            return ""
        if num.is_integer():
            return f"{int(num):02d}"
    except (TypeError, ValueError):
        pass
    return text


def tv_group_key(row: pd.Series) -> str:
    title = normalize_text(row.get("title_or_series"))
    season = _episode_component(row.get("season"))
    episode = _episode_component(row.get("episode"))
    year = _normalized_year(row.get("year"))
    return f"tv:{title}:{year}:s{season}:e{episode}" if year else f"tv:{title}:s{season}:e{episode}"


def build_group_key(row: pd.Series) -> str:
    return movie_group_key(row) if str(row.get("type", "")).lower() == "movie" else tv_group_key(row)


def detect_italian_audio_state(row: pd.Series, ds: pd.DataFrame | None, dx: pd.DataFrame | None) -> str:
    rk = str(row.get("rating_key") or "")
    pos = {"italian", "italiano", "ita", "it"}
    strong_neg = {"english", "eng", "french", "fre", "spanish", "spa", "japanese", "jpn", "german", "deu"}
    unknown_tokens = {"unknown", "und", "undefined", "none", "null", "empty", "audio", "track", "stream"}

    def _row_tokens(s: pd.Series, cols: list[str]) -> set[str]:
        text = " ".join(str(s.get(c, "")) for c in cols).lower()
        return set(re.findall(r"[a-z]+", text))

    def _scan(df: pd.DataFrame | None, cols_wanted: set[str]) -> str | None:
        if df is None:
            return None
        cols = {c.lower(): c for c in df.columns}
        matches = df[df[cols["rating_key"]].astype(str) == rk] if "rating_key" in cols else df
        if matches.empty:
            return None
        type_col = next((cols[k] for k in ["streamtype", "st_streamtype", "stream_type"] if k in cols), None)
        if type_col:
            matches = matches[matches[type_col].astype(str).str.lower().isin(["2", "audio"])]
        if matches.empty:
            return "unknown"
        scan_cols = [c for c in matches.columns if c.lower() in cols_wanted]
        seen_strong_neg = False
        for _, r in matches.iterrows():
            tokens = _row_tokens(r, scan_cols)
            if tokens & pos:
                return "yes"
            if tokens and not tokens.issubset(unknown_tokens) and (tokens & strong_neg):
                seen_strong_neg = True
        return "no" if seen_strong_neg else "unknown"

    stream_state = _scan(ds, {"lang", "language", "languagetag", "languagecode", "title", "displaytitle", "extendeddisplaytitle", "st_language", "st_languagetag", "st_languagecode", "st_title", "st_displaytitle", "st_extendeddisplaytitle"})
    xml_state = _scan(dx, {"dbg_audio_it_language", "dbg_audio_it_languagecode", "dbg_audio_it_title", "dbg_audio_it_displaytitle", "dbg_audio_it_extendeddisplaytitle", "dbg_audio_streams"})

    if stream_state == "yes":
        return "yes"
    if stream_state == "no":
        return "no"
    if xml_state in {"yes", "no"}:
        return xml_state
    if stream_state == "unknown" or xml_state == "unknown":
        return "unknown"

    try:
        it_bitrate = float(row.get("audio_it_bitrate_mbps") or 0)
    except (TypeError, ValueError):
        it_bitrate = 0
    if it_bitrate > 0 or str(row.get("audio_it_quality") or "").strip():
        return "yes"
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


def choose_primary_keeper(cluster: pd.DataFrame) -> pd.Series:
    scored = sorted([(idx, candidate_score(row)) for idx, row in cluster.iterrows()], key=lambda x: candidate_sort_key(x[1]))
    return cluster.loc[scored[0][0]]


def _allowed_special_keepers(cluster: pd.DataFrame) -> set[int]:
    eligible = cluster[cluster["source_tag"].isin(["full_disc", "dirtyhippie", "ai_upscale"]) & (~cluster["lowbit4k_penalized"])]
    if eligible.empty:
        return set()
    ranked = sorted([(idx, candidate_score(row)) for idx, row in eligible.iterrows()], key=lambda x: candidate_sort_key(x[1]))
    best_idx = ranked[0][0]
    keep = {best_idx}
    tags = set(eligible["source_tag"].tolist())
    if "full_disc" in tags and ({"dirtyhippie", "ai_upscale"} & tags):
        for idx, _ in ranked:
            if idx != best_idx and str(cluster.loc[idx, "source_tag"]) in {"full_disc", "dirtyhippie", "ai_upscale"}:
                keep.add(idx)
                break
    return keep


def _rank_indices(cluster: pd.DataFrame, indices: set[int] | list[int]) -> list[int]:
    return [
        idx for idx, _score in sorted(
            [(idx, candidate_score(cluster.loc[idx])) for idx in indices],
            key=lambda item: candidate_sort_key(item[1]),
        )
    ]


def _best_technical_keeper(cluster: pd.DataFrame) -> int | None:
    technical = cluster[(~cluster["source_tag"].isin(["full_disc", "dirtyhippie", "ai_upscale"])) & (~cluster["lowbit4k_penalized"])]
    if technical.empty:
        return None
    ranked = sorted([(idx, candidate_score(row)) for idx, row in technical.iterrows()], key=lambda x: candidate_sort_key(x[1]))
    return ranked[0][0]


def _choose_keep_indices(cluster: pd.DataFrame, keeper: pd.Series) -> set[int]:
    keep_indices: set[int] = {keeper.name}
    special_keepers = _allowed_special_keepers(cluster)
    best_technical = _best_technical_keeper(cluster)
    non_lowbit = cluster[~cluster["lowbit4k_penalized"]]
    full_disc_indices = set(non_lowbit[non_lowbit["source_tag"] == "full_disc"].index.tolist())
    dirty_ai_indices = set(non_lowbit[non_lowbit["source_tag"].isin(["dirtyhippie", "ai_upscale"])].index.tolist())

    if full_disc_indices and dirty_ai_indices and best_technical is not None:
        best_full_disc = _rank_indices(cluster, full_disc_indices)[0]
        best_dirty_ai = _rank_indices(cluster, dirty_ai_indices)[0]
        return {best_full_disc, best_dirty_ai, best_technical}

    if special_keepers:
        keep_indices.add(_rank_indices(cluster, special_keepers)[0])
        if best_technical is not None:
            keep_indices.add(best_technical)
    if len(keep_indices) > 2:
        keep_indices = set(_rank_indices(cluster, keep_indices)[:2])
    return keep_indices


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
    total_units = 7 + 1 + 2
    done_units += 1
    progress(done_units, total_units, "Workbook letto")
    progress(done_units, total_units, "Preparazione analisi duplicati")

    log("Normalizzazione titoli e percorsi...")
    done_units += 1
    progress(done_units, total_units, "Normalizzazione titoli e percorsi")
    df["normalized_title"] = df["title_or_series"].map(normalize_text)
    df["normalized_basename"] = df["file"].map(normalized_basename)
    df["basename"] = df["file"].map(basename)
    log("Calcolo durate...")
    done_units += 1
    progress(done_units, total_units, "Calcolo durate")
    df["duration_seconds"] = df["duration_hms"].map(_parse_duration_seconds)
    log("Creazione gruppi duplicati...")
    done_units += 1
    progress(done_units, total_units, "Creazione gruppi duplicati")
    df["group_key"] = df.apply(build_group_key, axis=1)
    df["cluster_index"] = 0
    movie_groups = [group for _, group in df.groupby("group_key") if str(group.iloc[0].get("type", "")).lower() == "movie"]
    total_units = 7 + max(len(movie_groups), 1) + 1 + 2
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
    group_sizes = df.groupby("group_key").size()
    duplicate_group_keys = set(group_sizes[group_sizes >= 2].index.tolist())
    duplicate_clusters = [cluster for (gk, _), cluster in df.groupby(["group_key", "cluster_index"]) if gk in duplicate_group_keys]
    duplicate_indices: set[int] = set()
    for cluster in duplicate_clusters:
        duplicate_indices.update(cluster.index)
    duplicate_index_list = sorted(duplicate_indices)
    duplicate_rows_total = len(duplicate_index_list)
    total_units = 7 + max(len(movie_groups), 1) + duplicate_rows_total + max(len(duplicate_clusters), 1) + 2
    done_units = min(done_units, total_units)
    if duplicate_rows_total == 0:
        log("Nessun gruppo duplicato trovato nel report selezionato.")
    else:
        log("Calcolo ranking video e sorgente su righe duplicate...")
        done_units += 1
        progress(done_units, total_units, "Calcolo ranking video e sorgente")
        df.loc[duplicate_index_list, "resolution_rank"] = df.loc[duplicate_index_list, "resolution"].map(resolution_rank)
        df.loc[duplicate_index_list, "hdr_rank"] = df.loc[duplicate_index_list, "hdr"].map(hdr_rank)
        df.loc[duplicate_index_list, "source_tag"] = df.loc[duplicate_index_list].apply(
            lambda r: source_tag_from_path(str(r.get("file") or ""), str(r.get("container") or "")),
            axis=1,
        )
        df.loc[duplicate_index_list, "source_rank"] = df.loc[duplicate_index_list, "source_tag"].map(
            lambda s: SOURCE_RANK.get(s, SOURCE_RANK["encode"])
        )
        log("Analisi audio italiano da Debug_Streams/Debug_XML su righe duplicate...")
        for i, idx in enumerate(duplicate_index_list, start=1):
            df.loc[idx, "italian_audio_state"] = detect_italian_audio_state(df.loc[idx], wb.debug_streams, wb.debug_xml)
            done_units = min(done_units + 1, total_units)
            if i % 250 == 0 or i == duplicate_rows_total:
                log(f"Analisi audio italiano: {i}/{duplicate_rows_total} righe duplicate")
                progress(done_units, total_units, "Analisi audio italiano")
        log("Calcolo punteggi audio su righe duplicate...")
        done_units += 1
        progress(done_units, total_units, "Calcolo punteggi audio")
        df.loc[duplicate_index_list, "audio_it_score"] = df.loc[duplicate_index_list].apply(
            lambda r: audio_score(parse_audio_quality(r.get("audio_it_quality"), r.get("audio_it_bitrate_mbps"))),
            axis=1,
        )
        df.loc[duplicate_index_list, "audio_en_score"] = df.loc[duplicate_index_list].apply(
            lambda r: audio_score(parse_audio_quality(r.get("audio_en_quality"), r.get("audio_en_bitrate_mbps"))),
            axis=1,
        )
    rows = []
    dup_groups = 0
    log("Classificazione gruppi duplicati...")
    total_clusters = len(duplicate_clusters)
    total_units = 7 + max(len(movie_groups), 1) + duplicate_rows_total + max(total_clusters, 1) + 2
    done_units = min(done_units, total_units)
    for processed, cluster in enumerate(duplicate_clusters, start=1):
        cluster = df.loc[cluster.index].copy()
        done_units = min(done_units + 1, total_units)
        if processed % 25 == 0 or processed == total_clusters:
            log(f"Classificazione gruppi: {processed}/{total_clusters}")
            progress(done_units, total_units, "Classificazione gruppi duplicati")
        dup_groups += 1
        has_good_1080 = ((cluster["resolution_rank"] == 1080) & (cluster["bitrate_mbps_video"].fillna(0) > 1.5)).any()
        cluster["lowbit4k_penalized"] = cluster.apply(lambda r: lowbit4k_penalty(str(r.get("type", "")).lower()=="movie", int(r["resolution_rank"]), float(r.get("bitrate_mbps_video") or 0), bool(has_good_1080)), axis=1)
        keeper = choose_primary_keeper(cluster)
        keep_indices = _choose_keep_indices(cluster, keeper)

        for idx, row in cluster.iterrows():
            action = "KEEP" if idx in keep_indices else "DELETE_SAFE"
            reason = ReasonBuilder.build_keep_reason() if action == "KEEP" else ""
            if action != "KEEP":
                row_it = parse_audio_quality(row.get("audio_it_quality"), row.get("audio_it_bitrate_mbps"))
                keep_it = parse_audio_quality(keeper.get("audio_it_quality"), keeper.get("audio_it_bitrate_mbps"))
                row_en = parse_audio_quality(row.get("audio_en_quality"), row.get("audio_en_bitrate_mbps"))
                keep_en = parse_audio_quality(keeper.get("audio_en_quality"), keeper.get("audio_en_bitrate_mbps"))
                same_tier = int(row.get("resolution_rank") or 0) == int(keeper.get("resolution_rank") or 0) and int(row.get("hdr_rank") or 0) == int(keeper.get("hdr_rank") or 0)
                rv = float(row.get("bitrate_mbps_video") or 0)
                kv = float(keeper.get("bitrate_mbps_video") or 0)
                rel = abs(rv-kv)/max(kv, 0.001)
                video_similar = same_tier and abs(rv-kv) <= 2.0 and rel <= 0.10

                if bool(row.get("lowbit4k_penalized")):
                    reason = f"regola 2160p: {ReasonBuilder.format_video_label(row)} sotto 12 Mbps con 1080p valida presente ; confronto keeper: {ReasonBuilder.format_video_label(keeper)}"
                elif video_similar and audio_better(row_it, keep_it, "it"):
                    action = "REVIEW_MANUAL"
                    reason = f"video simile: {ReasonBuilder.format_video_label(row)} ≈ {ReasonBuilder.format_video_label(keeper)} ; audio IT migliore sul file da valutare: {ReasonBuilder.format_audio_it_label(row)} > {ReasonBuilder.format_audio_it_label(keeper)} ; vantaggi incrociati: video vs audio/sorgente"
                elif video_similar and audio_better(row_en, keep_en, "en") and (not audio_better(row_it, keep_it, "it")):
                    action = "DELETE_PROPOSED"
                    reason = f"video simile: {ReasonBuilder.format_video_label(row)} ≈ {ReasonBuilder.format_video_label(keeper)} ; audio IT equivalente: {ReasonBuilder.format_audio_it_label(row)} = {ReasonBuilder.format_audio_it_label(keeper)} ; vantaggio residuo audio EN sul file da valutare: {ReasonBuilder.format_audio_en_label(row)} > {ReasonBuilder.format_audio_en_label(keeper)} ; resta un vantaggio secondario audio EN"
                else:
                    reason = f"video inferiore: {ReasonBuilder.format_video_label(row)} < {ReasonBuilder.format_video_label(keeper)} ; audio IT inferiore: {ReasonBuilder.format_audio_it_label(row)} < {ReasonBuilder.format_audio_it_label(keeper)} ; sorgente diversa ({row.get('source_tag')} vs {keeper.get('source_tag')})"

            rows.append({**row.to_dict(), "title_or_episode": row.get("episode_title") or row.get("title_or_series"), "file_path": row.get("file"), "keep_reference": keeper.get("file"), "final_action": action, "reason": reason})

        cluster_rows = [r for r in rows if r["group_key"] == cluster.iloc[0]["group_key"] and r["cluster_index"] == cluster.iloc[0]["cluster_index"]]
        if len(cluster_rows) == 1 and cluster_rows[0]["final_action"] == "KEEP":
            cluster_rows[0]["reason"] = ReasonBuilder.build_conserva_reason()
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
        manual_clusters = out_df.groupby(["group_key", "cluster_index"])["final_action"].transform(lambda s: (s == "REVIEW_MANUAL").any())
        movie_cut_conserva = out_df.groupby("group_key")["cluster_index"].transform("nunique") > 1
        out_df.loc[movie_cut_conserva & ~manual_clusters, "group_status"] = "CONSERVA"
    keep_count = int((out_df.final_action == "KEEP").sum()) if not out_df.empty else 0
    delete_safe_count = int((out_df.final_action == "DELETE_SAFE").sum()) if not out_df.empty else 0
    delete_proposed_count = int((out_df.final_action == "DELETE_PROPOSED").sum()) if not out_df.empty else 0
    manual_count = int((out_df.final_action == "REVIEW_MANUAL").sum()) if not out_df.empty else 0
    log(
        "Conteggi finali - "
        f"KEEP: {keep_count}, DELETE_SAFE: {delete_safe_count}, "
        f"DELETE_PROPOSED: {delete_proposed_count}, REVIEW_MANUAL: {manual_count}"
    )
    summary = pd.DataFrame({"metrica":["policy_version","policy_coverage_note","inventory_file","generated_at","total_rows","duplicate_groups","keep_count","delete_safe_count","delete_proposed_count","manual_count","conserva_count","debug_streams_used","debug_xml_used"],"valore":[POLICY_VERSION,"prima integrazione: alcune regole avanzate ancora parziali",str(inventory_path),datetime.now().isoformat(timespec="seconds"),len(df),dup_groups,keep_count,delete_safe_count,delete_proposed_count,manual_count,(out_df.drop_duplicates(["group_key", "cluster_index"]).query("group_status == 'CONSERVA'").shape[0] if not out_df.empty else 0),wb.debug_streams is not None,wb.debug_xml is not None]})
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"report_duplicati_plex_classificato_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    log("Scrittura workbook finale...")
    done_units = min(done_units + 1, total_units)
    progress(done_units, total_units, "Scrittura workbook finale")
    write_duplicate_report(out_path, summary, out_df)
    log(f"Report scritto: {out_path}")
    progress(total_units, total_units, "Report duplicati completato")
    return out_path
