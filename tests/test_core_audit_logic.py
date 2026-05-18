import types
from contextlib import nullcontext
from plex_inventory_app.core import InventoryRunner, build_output_columns


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def make_runner():
    r = InventoryRunner.__new__(InventoryRunner)
    r.config = Obj(fast_mode=True)
    r.duration_cache = {}
    r.duration_source_cache = {}
    r.mtx = nullcontext()
    r.metrics = {"duration_cache_hit": 0}
    r.get_attr = InventoryRunner.get_attr
    r.get_int = InventoryRunner.get_int.__get__(r, InventoryRunner)
    r.parse_required_bandwidths_first_mbps = InventoryRunner.parse_required_bandwidths_first_mbps
    r.kbps_to_mbps = InventoryRunner.kbps_to_mbps.__get__(r, InventoryRunner)
    r.stream_bitrate_mbps = InventoryRunner.stream_bitrate_mbps.__get__(r, InventoryRunner)
    r.compute_overhead_mbps = InventoryRunner.compute_overhead_mbps.__get__(r, InventoryRunner)
    r.select_primary_video_stream = InventoryRunner.select_primary_video_stream.__get__(r, InventoryRunner)
    return r


def test_by_file_keeps_duplicates_and_ambiguous():
    r = make_runner()
    bundle = {"parts": {"1": {"part": {}, "media": {}}, "2": {"part": {}, "media": {}}}, "by_file": {"/a.mkv": ["1", "2"]}, "by_base": {"a.mkv": ["1", "2"]}}
    r.fetch_item_xml_bundle = lambda item: bundle
    info, source = InventoryRunner.find_part_info_from_bundle(r, Obj(), Obj(file="/a.mkv", id=""), return_source=True)
    assert info is None
    assert source == "xml_ambiguous"


def test_resolution_prefers_stream_height():
    r = make_runner()
    st = Obj(streamType=1, height=1080, codedHeight=None)
    r.get_video_streams = lambda item, part: [st]
    res, src = InventoryRunner.detect_resolution(r, item=Obj(), media=Obj(videoResolution="720"), part=Obj())
    assert res == "1080p"
    assert src == "stream_height"


def test_duration_prefers_secure_xml():
    r = make_runner()
    item = Obj(type="movie", duration=6502000)
    media = Obj(duration=6502000)
    part = Obj(file="/x.mkv", duration=6502000)
    xml = {"part": {"duration": "6963000"}, "media": {}}
    d = InventoryRunner.robust_duration_ms(r, item, media, part, xml_info=xml, part_match_source="xml_part_id")
    assert d == 6963000
    assert r.duration_source_cache["/x.mkv"] == "xml_part"


def test_bitrate_xml_rejected():
    r = make_runner()
    r.media_total_mbps_via_xml = lambda item, part, xml_info=None: 0.078
    r.get_video_streams = lambda i, p: []
    r.get_audio_streams = lambda i, p: []
    r.get_subtitle_streams = lambda i, p: []
    out = InventoryRunner.compute_bitrates_and_size(
        r, Obj(), Obj(), Obj(size=7_541_000, container="mkv"), 8000,
        xml_info={"media": {}}, part_match_source="xml_part_id", resolution="1080p"
    )
    assert out[1] == "calc_xml_rejected"


def test_slim_columns_stable_and_audit_only_debug_or_full():
    slim = build_output_columns("SLIM_BUDGET", "HMS", include_audit=False)
    assert "bitrate_total_xml_mbps" not in slim
    slim_dbg = build_output_columns("SLIM_BUDGET", "HMS", include_audit=True)
    assert "bitrate_total_xml_mbps" in slim_dbg


def test_duration_short_movie_not_accepted_as_fallback_item():
    r = make_runner()
    item = Obj(type="movie", duration=30000)
    media = Obj(duration=None)
    part = Obj(file="/short.mkv", duration=None, container="mkv", size=0)
    r.media_total_mbps_via_xml = lambda *args, **kwargs: None
    d = InventoryRunner.robust_duration_ms(r, item, media, part, xml_info=None, part_match_source="xml_missing")
    assert d == 0
    assert r.duration_source_cache["/short.mkv"] == "missing"


def test_xml_verify_video_does_not_bypass_plausibility_checks():
    r = make_runner()
    r.config.xml_verify_video = True
    r.config.video_verify_tol = 0.0
    r.config.output_profile = "FULL"
    r.config.duration_output = "HMS"
    r.config.skip_short_clips = False
    r.config.run_preset = "FAST_PRECISE"
    r.config.debug = False
    r.cancel_event = Obj(is_set=lambda: False)
    r.rows = []
    r.output_columns = build_output_columns("FULL", "HMS", include_audit=True)
    r.mtx = nullcontext()
    r.metrics = {"rows_created": 0, "jobs_done": 0}
    r.detect_hdr_robusto = lambda *args, **kwargs: "SDR"
    r.find_part_info_from_bundle = lambda *args, **kwargs: ({"media": {}, "part": {}, "streams": []}, "xml_part_id") if kwargs.get("return_source") else {"media": {}, "part": {}, "streams": []}
    r.detect_resolution = lambda **kwargs: ("1080p", "stream_height")
    r.robust_duration_ms = lambda *args, **kwargs: 1000
    r.compute_bitrates_and_size = lambda *args, **kwargs: (8.0, "calc", None, 7.0, 0.5, 0.0, 1000, 1.0, None, 0.5, 0, 1, 8.0, 0.2, "", "estimated")
    r.fetch_video_bitrate_via_xml = lambda *args, **kwargs: 20.0
    r.get_show_meta_for_episode = lambda item: None
    r.get_ids_rating_from_xml = lambda item: (None, None, None)
    r.get_ids_and_rating = lambda item: (None, None, None)
    r.get_genres_from_xml = lambda item: ""
    r.get_genres_from_entity = lambda item: ""
    r.get_audio_streams = lambda *args, **kwargs: []
    r.pick_best_audio_it_en = lambda *args, **kwargs: ((None, ""), (None, ""))
    r.get_series_title_for_episode = lambda item: ""
    item = Obj(ratingKey="1", title="Movie", year=2020, addedAt=None, type="movie")
    media = Obj(videoCodec="h264", id="10")
    part = Obj(container="mkv", file="/v.mkv", id="20")
    InventoryRunner.add_row_from_part(r, item, media, part, "Movie")
    assert r.rows
    row = r.rows[0]
    assert row["bitrate_mbps_video_final"] != 20.0


def test_video_source_required_bandwidths_when_no_direct_bitrate():
    r = make_runner()
    r.media_total_mbps_via_xml = lambda *args, **kwargs: None
    st = Obj(streamType=1, bitrate=None, requiredBandwidths="5500,4000", height=1080)
    r.get_video_streams = lambda i, p: [st]
    r.get_audio_streams = lambda i, p: []
    r.get_subtitle_streams = lambda i, p: []
    out = InventoryRunner.compute_bitrates_and_size(
        r, Obj(), Obj(), Obj(size=5_000_000, container="mkv"), 5000,
        xml_info=None, part_match_source="xml_missing", resolution="1080p"
    )
    assert out[-1] == "stream_requiredBandwidths"
