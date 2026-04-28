from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def apply(core_mod) -> None:
    Runner = core_mod.InventoryRunner
    original = Runner.add_row_from_part

    def call(runner: Any, name: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
        fn = getattr(runner, name, None)
        if not callable(fn):
            return default
        try:
            return fn(*args, **kwargs)
        except Exception:
            return default

    def round3(value: Any) -> Any:
        try:
            return None if value is None else round(float(value), 3)
        except Exception:
            return value

    def json_cell(runner: Any, value: Any) -> str:
        text = call(runner, "json_dumps_safe", value, default=str(value))
        return call(runner, "clip_excel_cell", text, default=text)

    def keys(data: dict[str, Any] | None) -> str:
        try:
            return "|".join(sorted((data or {}).keys()))
        except Exception:
            return ""

    def keys_many(items: list[dict[str, Any]]) -> str:
        try:
            return "|".join(sorted({k for d in items for k in (d or {}).keys()}))
        except Exception:
            return ""

    def stream_type(st: Any, data: dict[str, Any] | None = None) -> int | None:
        raw = (data or {}).get("streamType") if isinstance(data, dict) else None
        if raw is None:
            raw = getattr(st, "streamType", None)
        try:
            return int(raw) if raw is not None else None
        except Exception:
            return None

    def added_at(item: Any):
        raw = getattr(item, "addedAt", None)
        if isinstance(raw, datetime):
            if raw.tzinfo is None:
                raw = raw.replace(tzinfo=timezone.utc)
            try:
                raw = raw.astimezone(core_mod.TZ_MILAN)
            except Exception:
                pass
            return raw.strftime("%Y-%m-%d %H:%M:%S")
        return None

    def find_debug_row(runner: Any, rating_key: Any, file_path: str):
        for row in reversed(getattr(runner, "debug_rows", []) or []):
            if str(row.get("rating_key")) == str(rating_key) and str(row.get("file") or "") == str(file_path or ""):
                return row
        return None

    def replace_stream_rows(runner: Any, rating_key: Any, file_path: str, new_rows: list[dict[str, Any]]) -> None:
        rows = getattr(runner, "debug_stream_rows", None)
        if not isinstance(rows, list):
            return
        rows[:] = [
            r for r in rows
            if not (str((r or {}).get("rating_key")) == str(rating_key) and str((r or {}).get("file") or "") == str(file_path or ""))
        ]
        rows.extend(new_rows)

    def expand_debug(runner: Any, item: Any, media: Any, part: Any, kind: str) -> None:
        if not getattr(getattr(runner, "config", None), "debug", False):
            return

        rating_key = getattr(item, "ratingKey", None)
        file_path = getattr(part, "file", "") or ""
        with runner.mtx:
            base = find_debug_row(runner, rating_key, file_path)
            if base is None:
                return

        title_or_series = getattr(item, "title", "") if kind == "Movie" else call(runner, "get_series_title_for_episode", item, default="")
        season = None if kind == "Movie" else getattr(item, "parentIndex", None)
        episode = None if kind == "Movie" else getattr(item, "index", None)
        episode_title = "" if kind == "Movie" else getattr(item, "title", "")
        res = call(runner, "norm_res", media, default=getattr(media, "videoResolution", "") or "")
        vcodec = getattr(media, "videoCodec", "") or ""
        container = (getattr(part, "container", "") or "").lower()

        item_dict = call(runner, "obj_attribs_dict", item, default={}) or {}
        media_dict = call(runner, "obj_attribs_dict", media, default={}) or {}
        part_dict = call(runner, "obj_attribs_dict", part, default={}) or {}

        a_streams = call(runner, "get_audio_streams", item, part, default=[]) or []
        v_streams = call(runner, "get_video_streams", item, part, default=[]) or []
        s_streams = call(runner, "get_subtitle_streams", item, part, default=[]) or []
        all_streams = call(runner, "safe_streams", item, part, default=[]) or []

        audio_dicts = [call(runner, "stream_attribs_dict", st, default={}) or {} for st in a_streams]
        video_dicts = [call(runner, "stream_attribs_dict", st, default={}) or {} for st in v_streams]
        subtitle_dicts = [call(runner, "stream_attribs_dict", st, default={}) or {} for st in s_streams]
        other_dicts: list[dict[str, Any]] = []
        for st in all_streams:
            data = call(runner, "stream_attribs_dict", st, default={}) or {}
            if stream_type(st, data) not in (1, 2, 3):
                other_dicts.append(data)

        audio_summary = []
        for st in a_streams:
            lg = call(runner, "stream_lang_code", st, default="?") or "?"
            lab = call(runner, "audio_quality_label", st, default="") or ""
            ch = call(runner, "get_int", st, "audioChannelCount", None, default=None) or call(runner, "get_int", st, "channels", None, default=0) or 0
            audio_summary.append(f"{lg}:{lab}({ch})")

        try:
            (_it_pack, it_pick), (_en_pack, en_pick) = call(
                runner, "pick_best_audio_it_en", a_streams, file_path=file_path, return_streams=True,
                default=((None, None), (None, None))
            )
        except Exception:
            it_pick, en_pick = None, None

        primary = call(runner, "select_primary_video_stream", v_streams, default=None)
        color_trc = call(runner, "get_attr", primary, "colorTrc", "", default="") if primary is not None else ""
        t_xml = call(runner, "media_total_mbps_via_xml", item, part, default=None)
        v_xml = call(runner, "fetch_video_bitrate_via_xml", item, part, default=None)
        hdr = base.get("hdr") or call(runner, "detect_hdr_robusto", item, media, part, default="")
        dur_src = getattr(runner, "duration_source_cache", {}).get(file_path, "unknown")

        updates: dict[str, Any] = {
            "type": kind,
            "rating_key": rating_key,
            "title_or_series": base.get("title_or_series") or title_or_series,
            "season": base.get("season", season),
            "episode": base.get("episode", episode),
            "episode_title": base.get("episode_title") or episode_title,
            "year": base.get("year", getattr(item, "year", None)),
            "added_at_milan": base.get("added_at_milan") or added_at(item),
            "resolution": base.get("resolution") or res,
            "hdr": hdr,
            "videoCodec": base.get("videoCodec") or vcodec,
            "container": base.get("container") or container,
            "file": file_path,
            "dbg_duration_source": base.get("dbg_duration_source") or dur_src,
            "dbg_total_source": base.get("dbg_total_source") or base.get("bitrate_total_source"),
            "dbg_media_bitrate_mbps_xml": base.get("dbg_media_bitrate_mbps_xml") or round3(t_xml),
            "dbg_video_bitrate_mbps_xml": base.get("dbg_video_bitrate_mbps_xml") or round3(v_xml),
            "dbg_media_vdr": getattr(media, "videoDynamicRange", "") or base.get("dbg_media_vdr", ""),
            "dbg_video_colorTrc": color_trc or base.get("dbg_video_colorTrc", ""),
            "dbg_num_audio": len(audio_dicts),
            "dbg_num_subs": len(subtitle_dicts),
            "dbg_audio_streams": " | ".join(audio_summary),
            "dbg_item_attribs_json": base.get("dbg_item_attribs_json") or json_cell(runner, item_dict),
            "dbg_item_attribs_keys": keys(item_dict),
            "dbg_media_attribs_json": base.get("dbg_media_attribs_json") or json_cell(runner, media_dict),
            "dbg_media_attribs_keys": keys(media_dict),
            "dbg_part_attribs_json": base.get("dbg_part_attribs_json") or json_cell(runner, part_dict),
            "dbg_part_attribs_keys": keys(part_dict),
            "dbg_num_video_streams": len(video_dicts),
            "dbg_num_subtitle_streams": len(subtitle_dicts),
            "dbg_num_other_streams": len(other_dicts),
            "dbg_video_streams_all_json": base.get("dbg_video_streams_all_json") or json_cell(runner, video_dicts),
            "dbg_video_streams_all_keys": keys_many(video_dicts),
            "dbg_subtitle_streams_all_json": base.get("dbg_subtitle_streams_all_json") or json_cell(runner, subtitle_dicts),
            "dbg_subtitle_streams_all_keys": keys_many(subtitle_dicts),
            "dbg_other_streams_all_json": base.get("dbg_other_streams_all_json") or json_cell(runner, other_dicts),
            "dbg_other_streams_all_keys": keys_many(other_dicts),
            "dbg_audio_streams_all_json": base.get("dbg_audio_streams_all_json") or json_cell(runner, audio_dicts),
            "dbg_audio_streams_all_keys": keys_many(audio_dicts),
            "dbg_audio_pick_it_json": json_cell(runner, call(runner, "stream_attribs_dict", it_pick, default={}) or {}) if it_pick else base.get("dbg_audio_pick_it_json", ""),
            "dbg_audio_pick_en_json": json_cell(runner, call(runner, "stream_attribs_dict", en_pick, default={}) or {}) if en_pick else base.get("dbg_audio_pick_en_json", ""),
        }

        for prefix, pick in (("dbg_audio_it", it_pick), ("dbg_audio_en", en_pick)):
            if pick is None:
                continue
            updates.update({
                f"{prefix}_id": call(runner, "get_attr", pick, "id", None, default=None),
                f"{prefix}_index": call(runner, "get_attr", pick, "index", None, default=None),
                f"{prefix}_codec": call(runner, "get_attr", pick, "codec", None, default=None),
                f"{prefix}_channels": call(runner, "get_attr", pick, "audioChannelCount", None, default=None) or call(runner, "get_attr", pick, "channels", None, default=None),
                f"{prefix}_channelLayout": call(runner, "get_attr", pick, "channelLayout", None, default=None),
                f"{prefix}_bitrate_kbps": call(runner, "get_attr", pick, "bitrate", None, default=None),
                f"{prefix}_samplingRate": call(runner, "get_attr", pick, "samplingRate", None, default=None),
                f"{prefix}_bitDepth": call(runner, "get_attr", pick, "bitDepth", None, default=None),
                f"{prefix}_profile": call(runner, "get_attr", pick, "profile", None, default=None),
                f"{prefix}_language": call(runner, "get_attr", pick, "language", None, default=None),
                f"{prefix}_languageCode": call(runner, "stream_lang_code", pick, default=None),
                f"{prefix}_title": call(runner, "get_attr", pick, "title", None, default=None),
                f"{prefix}_displayTitle": call(runner, "get_attr", pick, "displayTitle", None, default=None),
                f"{prefix}_extendedDisplayTitle": call(runner, "get_attr", pick, "extendedDisplayTitle", None, default=None),
                f"{prefix}_selected": call(runner, "get_attr", pick, "selected", None, default=None),
                f"{prefix}_default": call(runner, "get_attr", pick, "default", None, default=None),
            })

        for i, data in enumerate(audio_dicts, 1):
            for k, v in (data or {}).items():
                updates[f"dbg_audio_{i}_{k}"] = v
        for i, data in enumerate(video_dicts, 1):
            for k, v in (data or {}).items():
                updates[f"dbg_video_{i}_{k}"] = v
        for i, data in enumerate(other_dicts, 1):
            for k, v in (data or {}).items():
                updates[f"dbg_other_{i}_{k}"] = v

        stream_rows: list[dict[str, Any]] = []
        type_counts: dict[str, int] = {}
        for pos_all, st in enumerate(all_streams, 1):
            data = call(runner, "stream_attribs_dict", st, default={}) or {}
            stype = stream_type(st, data)
            label = {1: "video", 2: "audio", 3: "subtitle"}.get(stype, "other")
            type_counts[label] = type_counts.get(label, 0) + 1
            row = {
                "type": kind,
                "rating_key": rating_key,
                "title_or_series": base.get("title_or_series") or title_or_series,
                "season": base.get("season", season),
                "episode": base.get("episode", episode),
                "episode_title": base.get("episode_title") or episode_title,
                "year": base.get("year", getattr(item, "year", None)),
                "added_at_milan": base.get("added_at_milan") or added_at(item),
                "resolution": base.get("resolution") or res,
                "hdr": hdr,
                "videoCodec": base.get("videoCodec") or vcodec,
                "container": base.get("container") or container,
                "file": file_path,
                "media_id": media_dict.get("id") if isinstance(media_dict, dict) else None,
                "part_id": part_dict.get("id") if isinstance(part_dict, dict) else None,
                "stream_pos_all": pos_all,
                "stream_pos_type": type_counts[label],
                "streamType": stype,
                "streamTypeLabel": label,
                "stream_attribs_json": json_cell(runner, data),
            }
            for k, v in (data or {}).items():
                row[f"st_{k}"] = v
            stream_rows.append(row)

        with runner.mtx:
            target = find_debug_row(runner, rating_key, file_path)
            if target is not None:
                target.update(updates)
            replace_stream_rows(runner, rating_key, file_path, stream_rows)

    def wrapped(self: Any, item: Any, media: Any, part: Any, kind: str):
        result = original(self, item, media, part, kind)
        try:
            expand_debug(self, item, media, part, kind)
        except Exception as exc:
            try:
                self.log(f"[WARN] Debug wide non completato per {getattr(item, 'title', '')}: {exc!r}")
            except Exception:
                pass
        return result

    Runner.add_row_from_part = wrapped
