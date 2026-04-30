from __future__ import annotations

from datetime import datetime
from pathlib import Path
import pandas as pd

from .duplicate_policy_v12 import POLICY_VERSION, basename, hdr_rank, lowbit4k_penalty, normalize_text, parse_audio_quality, resolution_rank, source_tag_from_path
from .duplicate_report_writer import write_duplicate_report

REQUIRED_COLUMNS = ["type","title_or_series","season","episode","episode_title","year","resolution","hdr","videoCodec","container","duration_hms","bitrate_mbps_video","audio_it_bitrate_mbps","audio_it_quality","audio_en_bitrate_mbps","audio_en_quality","size_gib","imdb_id","rating_key","file"]


def _parse_duration_seconds(value: str) -> int:
    if not value or not isinstance(value, str) or ":" not in value:
        return 0
    parts = [int(x) for x in value.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def load_inventory_workbook(path: Path) -> pd.DataFrame:
    sheets = pd.read_excel(path, sheet_name=None)
    if "Library" not in sheets:
        raise ValueError("Workbook inventario non valido: manca il foglio 'Library'.")
    df = sheets["Library"].copy()
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Colonne obbligatorie mancanti nel foglio Library: {', '.join(missing)}")
    return df


def _group_key(row: pd.Series) -> str:
    t = str(row.get("type", "")).lower()
    title = normalize_text(row.get("title_or_series"))
    year = str(row.get("year") or "").strip()
    if t == "movie":
        if str(row.get("imdb_id") or "").strip():
            return f"movie:imdb:{row['imdb_id']}"
        if str(row.get("tmdb_id") or "").strip():
            return f"movie:tmdb:{row['tmdb_id']}"
        return f"movie:titleyear:{title}:{year}"
    season = str(row.get("season") or "")
    episode = str(row.get("episode") or "")
    if year:
        return f"tv:{title}:{year}:s{season}:e{episode}"
    return f"tv:{title}:s{season}:e{episode}"


def analyze_duplicates(inventory_path: Path, output_dir: Path, log_callback=None) -> Path:
    log = log_callback or (lambda _msg: None)
    log(f"Caricamento report: {inventory_path}")
    df = load_inventory_workbook(inventory_path)
    log(f"Righe lette: {len(df)}")
    df = df.copy()
    df["group_key"] = df.apply(_group_key, axis=1)
    df["duration_seconds"] = df["duration_hms"].apply(_parse_duration_seconds)
    df["resolution_rank"] = df["resolution"].map(lambda x: resolution_rank(str(x)))
    df["hdr_rank"] = df["hdr"].map(lambda x: hdr_rank(str(x)))
    df["source_tag"] = df["file"].map(source_tag_from_path)
    df["source_rank"] = df["source_tag"].map(lambda x: {"full_disc": 6, "dirtyhippie": 5, "ai_upscale": 5, "remux": 4, "bluray": 3, "web": 2}.get(x, 1))
    df["basename"] = df["file"].map(basename)
    df["italian_audio_state"] = df["audio_it_quality"].map(lambda x: "yes" if str(x).strip() else "unknown")

    results = []
    dup_groups = 0
    for gk, group in df.groupby("group_key"):
        if len(group) < 2:
            continue
        dup_groups += 1
        has_good_1080 = ((group["resolution_rank"] == 3) & (group["bitrate_mbps_video"].fillna(0) > 1.5)).any()
        scores = []
        for idx, row in group.iterrows():
            it = parse_audio_quality(row.get("audio_it_quality"), row.get("audio_it_bitrate_mbps"))
            en = parse_audio_quality(row.get("audio_en_quality"), row.get("audio_en_bitrate_mbps"))
            video = float(row.get("bitrate_mbps_video") or 0)
            penalty = 1 if lowbit4k_penalty(str(row.get("type")).lower() == "movie", int(row.get("resolution_rank") or 0), video, bool(has_good_1080)) else 0
            special_bonus = 1 if row.get("source_tag") in {"full_disc", "dirtyhippie", "ai_upscale"} else 0
            score = (int(row["resolution_rank"]) - penalty, int(row["hdr_rank"]), video, it.tier, it.channels, it.bitrate, special_bonus, int(row["source_rank"]), en.tier, en.bitrate)
            scores.append((idx, score))
        keeper_idx = sorted(scores, key=lambda x: x[1], reverse=True)[0][0]
        keeper = group.loc[keeper_idx]
        for idx, row in group.iterrows():
            action = "KEEP" if idx == keeper_idx or row.get("source_tag") in {"full_disc", "dirtyhippie", "ai_upscale"} else "DELETE_SAFE"
            reason = "versione tenuta con le regole attuali" if action == "KEEP" else "differenze contenute: copia ridondante"
            if action != "KEEP" and float(row.get("audio_en_bitrate_mbps") or 0) > float(keeper.get("audio_en_bitrate_mbps") or 0):
                action = "DELETE_PROPOSED"
                reason = "vantaggio residuo su audio EN ; differenze contenute: copia ridondante"
            results.append({**row.to_dict(), "group_status": "DUPLICATE", "cluster_index": 0, "title_or_episode": row.get("episode_title") or row.get("title_or_series"), "final_action": action, "reason": reason, "file_path": row.get("file"), "keep_reference": keeper.get("file"), "lowbit4k_penalized": bool(lowbit4k_penalty(str(row.get("type")).lower() == "movie", int(row.get("resolution_rank") or 0), float(row.get("bitrate_mbps_video") or 0), bool(has_good_1080)))})
    out_df = pd.DataFrame(results)
    if out_df.empty:
        out_df = pd.DataFrame(columns=["group_key","group_status","cluster_index","title_or_episode","type","resolution","hdr","bitrate_mbps_video","audio_it_quality","audio_it_bitrate_mbps","audio_en_quality","audio_en_bitrate_mbps","source_tag","italian_audio_state","final_action","reason","basename","file_path","keep_reference","rating_key"])
    summary = pd.DataFrame({"metrica": ["policy_version","inventory_file","generated_at","total_rows","duplicate_groups","keep_count","delete_safe_count","delete_proposed_count","manual_count","conserva_count"],"valore":[POLICY_VERSION,str(inventory_path),datetime.now().isoformat(timespec="seconds"),len(df),dup_groups,int((out_df['final_action']== 'KEEP').sum()),int((out_df['final_action']== 'DELETE_SAFE').sum()),int((out_df['final_action']== 'DELETE_PROPOSED').sum()),int((out_df['final_action']== 'REVIEW_MANUAL').sum()) if 'final_action' in out_df else 0,int((out_df['final_action']== 'KEEP').sum())]})

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"report_duplicati_plex_classificato_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    write_duplicate_report(out_path, summary, out_df)
    log(f"Gruppi duplicati trovati: {dup_groups}")
    log(f"KEEP: {(out_df['final_action'] == 'KEEP').sum()} | DELETE_SAFE: {(out_df['final_action'] == 'DELETE_SAFE').sum()} | DELETE_PROPOSED: {(out_df['final_action'] == 'DELETE_PROPOSED').sum()} | REVIEW_MANUAL: {(out_df['final_action'] == 'REVIEW_MANUAL').sum()}")
    log(f"File generato: {out_path}")
    return out_path
