import types
from plex_inventory_app.core import InventoryRunner, build_output_columns


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def make_runner():
    r = InventoryRunner.__new__(InventoryRunner)
    r.config = Obj(fast_mode=True)
    r.duration_cache = {}
    r.duration_source_cache = {}
    r.mtx = types.SimpleNamespace(__enter__=lambda s: None, __exit__=lambda s, a, b, c: False)
    r.metrics = {"duration_cache_hit": 0}
    r.get_attr = InventoryRunner.get_attr
    r.get_int = InventoryRunner.get_int.__get__(r, InventoryRunner)
    r.parse_required_bandwidths_first_mbps = InventoryRunner.parse_required_bandwidths_first_mbps.__get__(r, InventoryRunner)
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
