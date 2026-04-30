from pathlib import Path
import pandas as pd

from plex_inventory_app.duplicate_analysis import load_inventory_workbook, analyze_duplicates, _group_key
from plex_inventory_app.duplicate_policy_v12 import source_tag_from_path, lowbit4k_penalty

def make_library_df():
    return pd.DataFrame([{"type":"movie","title_or_series":"Film A","season":"","episode":"","episode_title":"","year":2020,"resolution":"1080p","hdr":"SDR","videoCodec":"h264","container":"mkv","duration_hms":"01:30:00","bitrate_mbps_video":5.0,"audio_it_bitrate_mbps":0.6,"audio_it_quality":"DD 5.1","audio_en_bitrate_mbps":0.5,"audio_en_quality":"DD 5.1","size_gib":5.0,"imdb_id":"tt1","rating_key":"1","file":"/a.mkv"}])

def test_load_library_ok(tmp_path: Path):
    p = tmp_path / "in.xlsx"; make_library_df().to_excel(p, sheet_name="Library", index=False)
    assert len(load_inventory_workbook(p)) == 1

def test_missing_library(tmp_path: Path):
    p = tmp_path / "in.xlsx"; pd.DataFrame([{"a":1}]).to_excel(p, sheet_name="X", index=False)
    try:
        load_inventory_workbook(p); assert False
    except ValueError as e:
        assert "Library" in str(e)

def test_grouping_movie_imdb_tmdb_titleyear():
    r = make_library_df().iloc[0]; assert _group_key(r).startswith("movie:imdb")
    r2 = r.copy(); r2["imdb_id"]=""; r2["tmdb_id"]="99"; assert "tmdb" in _group_key(r2)
    r3 = r.copy(); r3["imdb_id"]=""; r3["tmdb_id"]=""; assert "titleyear" in _group_key(r3)

def test_grouping_tv_year_distinct():
    base = make_library_df().iloc[0].copy(); base["type"]="episode"; base["season"]=1; base["episode"]=1
    a = base.copy(); a["year"]=2020; b = base.copy(); b["year"]=2021
    assert _group_key(a) != _group_key(b)

def test_source_and_penalty():
    assert source_tag_from_path('/x/DirtyHippie/file.mkv') == 'dirtyhippie'
    assert lowbit4k_penalty(True, 5, 10.0, True)

def test_output_sheets(tmp_path: Path):
    df = pd.concat([make_library_df(), make_library_df().assign(file='/b.mkv', rating_key='2')], ignore_index=True)
    p = tmp_path / 'inv.xlsx'; df.to_excel(p, sheet_name='Library', index=False)
    out = analyze_duplicates(p, tmp_path)
    sheets = pd.ExcelFile(out).sheet_names
    assert set(["Sintesi","Da_eliminare","Da_verificare","Conserva","Tutte_le_decisioni"]).issubset(set(sheets))

def test_delete_proposed_case(tmp_path: Path):
    base = make_library_df()
    worse_en = base.assign(file="/c.mkv", rating_key="3", bitrate_mbps_video=4.9, audio_en_bitrate_mbps=0.9)
    df = pd.concat([base, worse_en], ignore_index=True)
    p = tmp_path / "inv.xlsx"; df.to_excel(p, sheet_name="Library", index=False)
    out = analyze_duplicates(p, tmp_path)
    all_df = pd.read_excel(out, sheet_name="Tutte_le_decisioni")
    assert "DELETE_PROPOSED" in set(all_df["final_action"])

def test_review_manual_case(tmp_path: Path):
    keep = make_library_df()
    cross = keep.assign(
        file="/d.mkv",
        rating_key="4",
        bitrate_mbps_video=4.6,
        audio_it_quality="DD+ 5.1",
        audio_it_bitrate_mbps=1.2,
    )
    df = pd.concat([keep, cross], ignore_index=True)
    p = tmp_path / "inv.xlsx"; df.to_excel(p, sheet_name="Library", index=False)
    out = analyze_duplicates(p, tmp_path)
    all_df = pd.read_excel(out, sheet_name="Tutte_le_decisioni")
    assert "REVIEW_MANUAL" in set(all_df["final_action"])
