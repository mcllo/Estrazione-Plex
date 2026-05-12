from pathlib import Path
import pandas as pd

from plex_inventory_app.duplicate_analysis import (
    load_inventory_workbook, analyze_duplicates, movie_group_key, tv_group_key, build_group_key,
    detect_italian_audio_state,
)
from plex_inventory_app.duplicate_policy_v12 import (
    source_tag_from_path,
    lowbit4k_penalty,
    parse_audio_quality,
    audio_better,
    resolution_rank,
    hdr_rank,
    normalize_text,
    candidate_score,
    candidate_sort_key,
    audio_codec_family,
)


def make_row(**kwargs):
    base = {"type":"movie","title_or_series":"Film A","season":"","episode":"","episode_title":"","year":2020,"resolution":"1080p","hdr":"SDR","videoCodec":"h264","container":"mkv","duration_hms":"01:30:00","bitrate_mbps_video":5.0,"audio_it_bitrate_mbps":0.6,"audio_it_quality":"DD 5.1","audio_en_bitrate_mbps":0.5,"audio_en_quality":"DD 5.1","size_gib":5.0,"imdb_id":"tt1","rating_key":"1","file":"/a.mkv"}
    base.update(kwargs)
    return base


def test_missing_library(tmp_path: Path):
    p = tmp_path / "in.xlsx"; pd.DataFrame([{"a":1}]).to_excel(p, sheet_name="X", index=False)
    try:
        load_inventory_workbook(p); assert False
    except ValueError as e:
        assert "Library" in str(e)


def test_grouping_keys():
    r = pd.Series(make_row())
    assert movie_group_key(r).startswith("movie:imdb")
    r2 = pd.Series(make_row(imdb_id="", tmdb_id="9")); assert "tmdb" in movie_group_key(r2)
    r3 = pd.Series(make_row(type="episode", season=1, episode=2, year=2020));
    r4 = pd.Series(make_row(type="episode", season=1, episode=2, year=2021));
    assert tv_group_key(r3) != tv_group_key(r4)
    assert build_group_key(r3).startswith("tv:")


def test_duration_split_60s(tmp_path: Path):
    df = pd.DataFrame([make_row(duration_hms="01:30:00", file="/a.mkv", rating_key="1"), make_row(duration_hms="01:31:05", file="/b.mkv", rating_key="2")])
    p = tmp_path / "in.xlsx"; df.to_excel(p, sheet_name="Library", index=False)
    out = analyze_duplicates(p, tmp_path)
    all_df = pd.read_excel(out, sheet_name="Tutte_le_decisioni")
    assert all_df.empty


def test_source_tags():
    assert source_tag_from_path('/x/BDMV/file.m2ts', 'm2ts') == 'full_disc'
    assert source_tag_from_path('/x/DirtyHippie/file.mkv') == 'dirtyhippie'
    assert source_tag_from_path('/x/ai_upscale/file.mkv') == 'ai_upscale'
    assert source_tag_from_path('/x/remux/file.mkv') == 'remux'
    assert source_tag_from_path('/x/web-dl/file.mkv') == 'web'
    assert source_tag_from_path('/x/bluray/file.mkv') == 'bluray'
    assert source_tag_from_path('/x/repack/file.mkv') == 'repack'
    assert source_tag_from_path('/x/other/file.mkv') == 'encode'


def test_italian_state_yes_unknown():
    row = pd.Series(make_row(audio_it_quality="", audio_it_bitrate_mbps=0, file="/movie.m2ts", container="m2ts"))
    ds = pd.DataFrame([{"rating_key":"1", "lang":"ita"}])
    assert detect_italian_audio_state(row, ds, None) == "yes"
    assert detect_italian_audio_state(row, None, None) == "unknown"


def test_audio_guardrails():
    dd = parse_audio_quality("DD 5.1", 0.9)
    ddp = parse_audio_quality("DD+ 5.1", 0.3)
    assert not audio_better(ddp, dd, "it")
    lossless = parse_audio_quality("TrueHD 5.1", 1.5)
    lossy = parse_audio_quality("DD+ 5.1", 1.6)
    assert not audio_better(lossy, lossless, "it")



def test_policy_handles_nan_values_without_crashing():
    resolution_rank(float("nan"))
    hdr_rank(float("nan"))
    normalize_text(float("nan"))
    source_tag_from_path(float("nan"), float("nan"))
    parse_audio_quality(float("nan"), float("nan"))

def test_lowbit4k_penalty_rule():
    assert lowbit4k_penalty(True, 2160, 10.0, True)
    assert not lowbit4k_penalty(True, 2160, 10.0, False)


def test_resolution_rank_matches_policy_v12_scale():
    assert resolution_rank("2160p") == 2160
    assert resolution_rank("4k") == 2160
    assert resolution_rank("1080p") == 1080
    assert resolution_rank("sd") == 360


def test_source_tags_match_policy_v12_patterns():
    assert source_tag_from_path("/x/ai-enhanced.mkv") == "ai_upscale"
    assert source_tag_from_path("/x/ai.upscaled.mkv") == "ai_upscale"
    assert source_tag_from_path("/x/NF.WEB-DL.mkv") == "web"
    assert source_tag_from_path("/x/UHDRip.release.mkv") == "bluray"
    assert source_tag_from_path("/x/info.release.mkv") != "web"


def test_dd_plus_does_not_beat_much_higher_bitrate_dd_same_channels():
    assert not audio_better(parse_audio_quality("DD+ 5.1", 0.3), parse_audio_quality("DD 5.1", 0.9), "it")


def test_lossy_does_not_beat_lossless_same_broad_tier():
    assert not audio_better(parse_audio_quality("DD+ 5.1", 1.6), parse_audio_quality("TrueHD 5.1", 1.0), "it")


def test_dd_plus_can_beat_dd_when_guardrail_cleared_and_score_delta_high():
    assert audio_better(parse_audio_quality("DD+ 7.1", 1.6), parse_audio_quality("DD 7.1", 0.7), "it")


def test_dtsx_is_classified_as_lossless_or_master():
    assert audio_codec_family("DTS:X 7.1") == "lossless_or_master"
    assert parse_audio_quality("DTS:X 7.1", 2.0).codec_score == 9.3


def test_audio_codec_score_matches_policy_values():
    assert parse_audio_quality("TrueHD Atmos 7.1", 4.0).codec_score == 9.5
    assert parse_audio_quality("DD+ Atmos 5.1", 0.768).codec_score == 6.7
    assert parse_audio_quality("DD 5.1", 0.640).codec_score == 5.7


def test_audio_bitrate_term_uses_mbps_to_kbps_and_caps():
    low = parse_audio_quality("DD 5.1", 0.1)
    high = parse_audio_quality("DD 5.1", 4.0)
    extreme = parse_audio_quality("DD 5.1", 100.0)
    assert high.total_score > low.total_score
    assert extreme.total_score - extreme.codec_score - 0.5 <= 2.5


def test_ma_substring_does_not_create_false_lossless():
    assert parse_audio_quality("German AAC 2.0", 0.2).codec_family != "lossless_or_master"


def test_candidate_sort_key_prefers_video_then_it_audio_then_source_then_en():
    row1 = pd.Series(make_row(file="/a.mkv", bitrate_mbps_video=8.0))
    row2 = pd.Series(make_row(file="/b.mkv", bitrate_mbps_video=7.0))
    for r in (row1, row2):
        r["resolution_rank"] = resolution_rank(r["resolution"])
        r["hdr_rank"] = hdr_rank(r["hdr"])
        r["source_tag"] = source_tag_from_path(r["file"], r["container"])
        r["source_rank"] = 6.0
        r["audio_it_score"] = (1, 1, 1, 1)
        r["audio_en_score"] = (1, 1, 1, 1)
        r["normalized_basename"] = r["file"]
        r["lowbit4k_penalized"] = False
    assert candidate_sort_key(candidate_score(row1)) < candidate_sort_key(candidate_score(row2))


def test_special_original_plus_best_technical_stays_conserva(tmp_path: Path):
    df = pd.DataFrame([
        make_row(file="/best.mkv", rating_key="1", resolution="1080p", bitrate_mbps_video=10.0),
        make_row(file="/special_dirtyhippie.mkv", rating_key="2", bitrate_mbps_video=5.0),
    ])
    p = tmp_path / "in.xlsx"; df.to_excel(p, sheet_name="Library", index=False)
    out = analyze_duplicates(p, tmp_path)
    all_df = pd.read_excel(out, sheet_name="Tutte_le_decisioni")
    assert (all_df["final_action"] == "KEEP").sum() >= 2
    assert (all_df["group_status"] == "CONSERVA").any()


def test_unknown_full_disc_language_does_not_trigger_no_italian_manual(tmp_path: Path):
    df = pd.DataFrame([
        make_row(file="/full_disc.m2ts", container="m2ts", rating_key="1", audio_it_quality=""),
        make_row(file="/norm.mkv", rating_key="2", audio_it_quality="DD 5.1"),
    ])
    p = tmp_path / "in.xlsx"; df.to_excel(p, sheet_name="Library", index=False)
    out = analyze_duplicates(p, tmp_path)
    all_df = pd.read_excel(out, sheet_name="Tutte_le_decisioni")
    assert not ((all_df["file_path"].str.contains("full_disc")) & (all_df["final_action"] == "REVIEW_MANUAL")).any()


def test_delete_proposed_only_for_residual_en_audio_advantage(tmp_path: Path):
    df = pd.DataFrame([
        make_row(file="/best.mkv", rating_key="1", bitrate_mbps_video=8.0, audio_en_bitrate_mbps=0.3),
        make_row(file="/enadv.mkv", rating_key="2", bitrate_mbps_video=7.9, audio_en_bitrate_mbps=1.2),
    ])
    p = tmp_path / "in.xlsx"; df.to_excel(p, sheet_name="Library", index=False)
    out = analyze_duplicates(p, tmp_path)
    all_df = pd.read_excel(out, sheet_name="Tutte_le_decisioni")
    assert "DELETE_PROPOSED" in set(all_df["final_action"])


def test_candidate_score_handles_nan_values():
    row = pd.Series(make_row())
    row["audio_it_score"] = float("nan")
    row["audio_en_score"] = None
    row["bitrate_mbps_video"] = float("nan")
    s = candidate_score(row)
    assert isinstance(s.video_bitrate, float)
    assert isinstance(s.audio_it_score, tuple)
    assert s.lowbit4k_penalized is False


def test_tv_group_key_normalizes_season_episode():
    r1 = pd.Series(make_row(type="episode", season=1, episode=2, year=2020))
    r2 = pd.Series(make_row(type="episode", season="01", episode="02", year=2020))
    r3 = pd.Series(make_row(type="episode", season="01", episode="02", year=2021))
    assert tv_group_key(r1) == tv_group_key(r2)
    assert tv_group_key(r1) != tv_group_key(r3)


def test_detect_italian_audio_state_streams_and_fallbacks():
    row = pd.Series(make_row(rating_key="1", file="/full_disc.m2ts", container="m2ts", audio_it_quality="", audio_it_bitrate_mbps=0))
    ds_yes = pd.DataFrame([{"rating_key": "1", "streamType": 2, "language": "ita"}])
    ds_no = pd.DataFrame([{"rating_key": "1", "streamType": 2, "language": "eng"}, {"rating_key": "1", "streamType": 2, "language": "fre"}])
    assert detect_italian_audio_state(row, ds_yes, None) == "yes"
    assert detect_italian_audio_state(row, ds_no, None) == "no"
    assert detect_italian_audio_state(row, None, None) == "unknown"
    row_lib = pd.Series(make_row(rating_key="2", audio_it_bitrate_mbps=0.2, audio_it_quality=""))
    assert detect_italian_audio_state(row_lib, None, None) == "yes"


def test_candidate_sort_key_handles_missing_audio_score():
    row = pd.Series(make_row())
    row["audio_it_score"] = None
    row["audio_en_score"] = "bad"
    row["resolution_rank"] = resolution_rank(row["resolution"])
    row["hdr_rank"] = hdr_rank(row["hdr"])
    row["source_rank"] = 5.0
    row["normalized_basename"] = "x"
    row["lowbit4k_penalized"] = False
    key = candidate_sort_key(candidate_score(row))
    assert isinstance(key, tuple)


def test_hdr_rank_recognizes_hlg_and_hdr10_plus():
    assert hdr_rank("hlg") == 1
    assert hdr_rank("hdr10 plus") == 3


def test_special_keep_not_more_than_two_in_ordinary_case(tmp_path: Path):
    df = pd.DataFrame([
        make_row(file="/best.mkv", rating_key="1", bitrate_mbps_video=9.0),
        make_row(file="/dirtyhippie.one.mkv", rating_key="2", bitrate_mbps_video=4.0),
        make_row(file="/ai-upscaled.two.mkv", rating_key="3", bitrate_mbps_video=4.1),
    ])
    p = tmp_path / "in.xlsx"; df.to_excel(p, sheet_name="Library", index=False)
    out = analyze_duplicates(p, tmp_path)
    all_df = pd.read_excel(out, sheet_name="Tutte_le_decisioni")
    assert (all_df["final_action"] == "KEEP").sum() <= 2


def test_full_disc_primary_also_keeps_best_conventional_technical(tmp_path: Path):
    df = pd.DataFrame([
        make_row(file="/full_disc_best.m2ts", container="m2ts", rating_key="1", bitrate_mbps_video=12.0, audio_it_quality=""),
        make_row(file="/tech_best.mkv", rating_key="2", bitrate_mbps_video=9.5, audio_it_quality="TrueHD 5.1", audio_it_bitrate_mbps=1.2),
        make_row(file="/tech_worse.mkv", rating_key="3", bitrate_mbps_video=7.0),
    ])
    p = tmp_path / "in.xlsx"; df.to_excel(p, sheet_name="Library", index=False)
    out = analyze_duplicates(p, tmp_path)
    all_df = pd.read_excel(out, sheet_name="Tutte_le_decisioni")
    keeps = all_df[all_df["final_action"] == "KEEP"]["file_path"].tolist()
    assert any("full_disc_best" in x for x in keeps)
    assert any("tech_best" in x for x in keeps)


def test_full_disc_dirtyhippie_and_best_technical_are_all_kept(tmp_path: Path):
    df = pd.DataFrame([
        make_row(file="/full_disc.m2ts", container="m2ts", rating_key="1", bitrate_mbps_video=11.5, audio_it_quality=""),
        make_row(file="/dirtyhippie.mkv", rating_key="2", bitrate_mbps_video=6.2),
        make_row(file="/best_technical.mkv", rating_key="3", bitrate_mbps_video=9.8, audio_it_quality="TrueHD 5.1", audio_it_bitrate_mbps=1.1),
    ])
    p = tmp_path / "in.xlsx"; df.to_excel(p, sheet_name="Library", index=False)
    out = analyze_duplicates(p, tmp_path)
    all_df = pd.read_excel(out, sheet_name="Tutte_le_decisioni")
    kept = all_df[all_df["final_action"] == "KEEP"]["file_path"].tolist()
    assert set(kept) == {"/full_disc.m2ts", "/dirtyhippie.mkv", "/best_technical.mkv"}
    assert (all_df["group_status"] == "CONSERVA").all()
    assert "REVIEW_MANUAL" not in set(all_df["final_action"])


def test_multiple_specials_without_full_disc_combo_still_limited(tmp_path: Path):
    df = pd.DataFrame([
        make_row(file="/best_technical.mkv", rating_key="1", bitrate_mbps_video=9.9),
        make_row(file="/dirtyhippie.mkv", rating_key="2", bitrate_mbps_video=5.1),
        make_row(file="/ai_upscale_1.mkv", rating_key="3", bitrate_mbps_video=5.0),
        make_row(file="/ai_upscale_2.mkv", rating_key="4", bitrate_mbps_video=4.9),
    ])
    p = tmp_path / "in.xlsx"; df.to_excel(p, sheet_name="Library", index=False)
    out = analyze_duplicates(p, tmp_path)
    all_df = pd.read_excel(out, sheet_name="Tutte_le_decisioni")
    assert (all_df["final_action"] == "KEEP").sum() <= 2


def test_keep_selection_is_deterministic(tmp_path: Path):
    df = pd.DataFrame([
        make_row(file="/full_disc.m2ts", container="m2ts", rating_key="1", bitrate_mbps_video=11.5, audio_it_quality=""),
        make_row(file="/dirtyhippie.mkv", rating_key="2", bitrate_mbps_video=6.2),
        make_row(file="/best_technical.mkv", rating_key="3", bitrate_mbps_video=9.8, audio_it_quality="TrueHD 5.1", audio_it_bitrate_mbps=1.1),
    ])
    p = tmp_path / "in.xlsx"; df.to_excel(p, sheet_name="Library", index=False)
    out1 = analyze_duplicates(p, tmp_path)
    out2 = analyze_duplicates(p, tmp_path)
    keeps1 = pd.read_excel(out1, sheet_name="Tutte_le_decisioni")
    keeps2 = pd.read_excel(out2, sheet_name="Tutte_le_decisioni")
    files1 = sorted(keeps1[keeps1["final_action"] == "KEEP"]["file_path"].tolist())
    files2 = sorted(keeps2[keeps2["final_action"] == "KEEP"]["file_path"].tolist())
    assert files1 == files2


def test_actions_and_sheets(tmp_path: Path):
    df = pd.DataFrame([
        make_row(file="/best.mkv", rating_key="1", resolution="1080p", bitrate_mbps_video=8.0, audio_en_bitrate_mbps=0.4),
        make_row(file="/enadv.mkv", rating_key="2", resolution="1080p", bitrate_mbps_video=7.9, audio_en_bitrate_mbps=1.2),
        make_row(file="/cross.mkv", rating_key="3", resolution="1080p", bitrate_mbps_video=7.7, audio_it_quality="TrueHD 5.1", audio_it_bitrate_mbps=1.4),
        make_row(file="/full_disc.m2ts", rating_key="4", container="m2ts", audio_it_quality=""),
    ])
    p = tmp_path / "in.xlsx"; df.to_excel(p, sheet_name="Library", index=False)
    out = analyze_duplicates(p, tmp_path)
    xls = pd.ExcelFile(out)
    assert set(["Sintesi","Da_eliminare","Da_verificare","Conserva","Tutte_le_decisioni"]).issubset(set(xls.sheet_names))
    all_df = pd.read_excel(out, sheet_name="Tutte_le_decisioni")
    assert {"KEEP", "DELETE_PROPOSED", "REVIEW_MANUAL"}.issubset(set(all_df["final_action"]))
