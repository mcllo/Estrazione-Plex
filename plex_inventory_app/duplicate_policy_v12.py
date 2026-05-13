from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re
from typing import Literal

POLICY_VERSION = "v12_normative_general_policy_tv_year_fix_audio_engineering"

RESOLUTION_RANK = {"2160": 2160, "4k": 2160, "1080": 1080, "720": 720, "576": 576, "480": 480, "sd": 360}
HDR_RANK = {"": 0, "sdr": 0, "hdr": 1, "hdr10": 2, "hdr10+": 3, "dolby vision": 4, "dv": 4, "hlg": 1}
SOURCE_RANK = {"full_disc": 9.0, "dirtyhippie": 8.5, "ai_upscale": 8.5, "remux": 8.0, "bluray": 6.5, "web": 6.0, "repack": 5.5, "encode": 5.0}
CODEC_SCORE = {"truehd_atmos": 9.5, "dts_x": 9.3, "truehd": 9.0, "dts_hd": 8.8, "flac_lpcm_pcm": 8.7, "ddp_atmos": 6.7, "ddp": 6.2, "dts": 6.0, "dd_atmos": 6.1, "dd": 5.7, "opus": 4.9, "aac": 4.8, "mp3": 4.0, "unknown": 3.8}


@dataclass(frozen=True)
class AudioScore:
    codec_key: str
    codec_family: Literal["lossless_or_master", "lossy", "unknown"]
    broad_tier: Literal["mono", "stereo_or_matrix", "surround", "high_surround_or_immersive_bed", "unknown"]
    channels: float
    bitrate: float
    codec_score: float
    total_score: float


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value)


def normalize_text(value: object) -> str:
    text = _safe_text(value).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"[^a-z0-9 ]", "", text)


def normalized_basename(path: object) -> str:
    return normalize_text(Path(_safe_text(path)).name)


def basename(path: object) -> str:
    return Path(_safe_text(path)).name


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        parsed = float(value)
        return default if math.isnan(parsed) else parsed
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if value is None:
            return default
        parsed = int(float(value))
        return default if isinstance(value, float) and math.isnan(value) else parsed
    except (TypeError, ValueError):
        return default


def _safe_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return (not (isinstance(value, float) and math.isnan(value))) and value != 0
    if isinstance(value, str):
        t = value.strip().lower()
        if t in {"true", "1", "yes", "y", "si", "sì"}:
            return True
        if t in {"false", "0", "no", "n"}:
            return False
    return default


def source_tag_from_path(file_path: object, container: object = None) -> str:
    low = _safe_text(file_path).lower()
    base = Path(low).name
    cont = _safe_text(container).lower()
    if "dirtyhippie" in low:
        return "dirtyhippie"
    if any(x in low for x in ["aiupscale", "ai_upscale", "ai-enhanced", "ai-upscaled", "ai.upscaled", "upscaled", "rife"]):
        return "ai_upscale"
    if cont in {"m2ts", "mpegts", "ts"} or base.endswith(".m2ts") or any(x in low for x in ["full disc", "full_disc", "bdmv", "complete blu"]):
        return "full_disc"
    if "remux" in low:
        return "remux"
    if any(x in low for x in ["web-dl", "webdl", "webrip", "web rip", "hmax", "amzn", "dsnp"]) or re.search(r"(?:^|[.\-\s\[])(nf)(?:$|[.\-\s\]])", low):
        return "web"
    if any(x in low for x in ["bluray", "blu-ray", "bdrip", "uhdrip"]):
        return "bluray"
    if "repack" in low:
        return "repack"
    return "encode"


def resolution_rank(value: object) -> int:
    text = _safe_text(value).lower()
    for key, rank in RESOLUTION_RANK.items():
        if key in text:
            return rank
    return 0


def hdr_rank(value: object) -> int:
    text = _safe_text(value).strip().lower()
    if text in {"dv", "dolby vision"}:
        return 4
    if text in {"hdr10+", "hdr10 plus", "hdr10plus"}:
        return 3
    if text == "hdr10":
        return 2
    if text == "hlg":
        return 1
    return HDR_RANK.get(text, 0)


def audio_codec_key(quality: object) -> str:
    q = _safe_text(quality).lower()
    if "truehd" in q and "atmos" in q:
        return "truehd_atmos"
    if "dts:x" in q or "dts-x" in q or "dts x" in q:
        return "dts_x"
    if "truehd" in q:
        return "truehd"
    if "dts-hd" in q or "dts hd" in q or re.search(r"dts\s*ma", q):
        return "dts_hd"
    if "flac" in q or "lpcm" in q or " pcm" in f" {q} ":
        return "flac_lpcm_pcm"
    if "atmos" in q and ("eac3" in q or "dd+" in q or "dolby digital plus" in q):
        return "ddp_atmos"
    if "atmos" in q and ("ac3" in q or re.search(r"\bdd\b", q) or "dolby digital" in q):
        return "dd_atmos"
    if "eac3" in q or "dd+" in q or "dolby digital plus" in q:
        return "ddp"
    if re.search(r"\bdts\b", q):
        return "dts"
    if "ac3" in q or re.search(r"\bdd\b", q) or "dolby digital" in q:
        return "dd"
    if "opus" in q:
        return "opus"
    if "aac" in q:
        return "aac"
    if "mp3" in q:
        return "mp3"
    return "unknown"


def audio_codec_family(codec_key: str) -> Literal["lossless_or_master", "lossy", "unknown"]:
    if codec_key not in CODEC_SCORE:
        codec_key = audio_codec_key(codec_key)
    if codec_key in {"truehd_atmos", "dts_x", "truehd", "dts_hd", "flac_lpcm_pcm"}:
        return "lossless_or_master"
    if codec_key in {"ddp_atmos", "ddp", "dts", "dd_atmos", "dd", "opus", "aac", "mp3"}:
        return "lossy"
    return "unknown"


def _channels_from_quality(quality: object) -> float:
    q = _safe_text(quality).lower()
    m = re.search(r"(\d(?:\.\d)?)", q)
    return float(m.group(1)) if m else 0.0


def broad_channel_tier(channels: float) -> Literal["mono", "stereo_or_matrix", "surround", "high_surround_or_immersive_bed", "unknown"]:
    ch = channels
    if ch <= 0:
        return "unknown"
    if ch <= 1.1:
        return "mono"
    if ch in {2.0, 3.0, 4.0}:
        return "stereo_or_matrix"
    if ch in {5.1, 6.1}:
        return "surround"
    if ch >= 7.1:
        return "high_surround_or_immersive_bed"
    return "unknown"


def _channel_bonus(channels: float) -> float:
    if channels >= 7.1:
        return 0.8
    if channels >= 6.1:
        return 0.65
    if channels >= 5.1:
        return 0.5
    if channels >= 4.0:
        return 0.25
    if channels >= 3.0:
        return 0.15
    if channels >= 2.0:
        return 0.1
    return 0.0


def parse_audio_quality(quality: object, bitrate_mbps: object) -> AudioScore:
    bitrate = max(_safe_float(bitrate_mbps, 0.0), 0.0)
    codec_key = audio_codec_key(quality)
    channels = _channels_from_quality(quality)
    codec_score = CODEC_SCORE.get(codec_key, 3.8)
    bitrate_kbps = bitrate * 1000.0
    total = codec_score + _channel_bonus(channels) + min(2.5, math.log(1.0 + bitrate_kbps) / 4.0)
    return AudioScore(codec_key, audio_codec_family(codec_key), broad_channel_tier(channels), channels, bitrate, codec_score, total)


def _tier_num(t: str) -> int:
    return {"unknown": 0, "mono": 1, "stereo_or_matrix": 2, "surround": 3, "high_surround_or_immersive_bed": 4}[t]


def audio_better(candidate: AudioScore, reference: AudioScore, language: str = "it") -> bool:
    if candidate.codec_key == "unknown" and reference.codec_key != "unknown":
        return False
    if candidate.codec_family == "lossy" and reference.codec_family == "lossless_or_master":
        delta_tier = _tier_num(candidate.broad_tier) - _tier_num(reference.broad_tier)
        if delta_tier <= 1:
            return False
    if candidate.codec_family == "lossy" and reference.codec_family == "lossy":
        if (candidate.channels == reference.channels or candidate.broad_tier == reference.broad_tier) and (candidate.bitrate * 1000.0) < 0.5 * (reference.bitrate * 1000.0):
            return False
    if candidate.codec_key == "ddp" and reference.codec_key == "dd" and candidate.channels == reference.channels:
        if (candidate.bitrate * 1000.0) * 2.0 <= (reference.bitrate * 1000.0):
            return False
    threshold = 0.35 if language == "it" else 0.25
    return (candidate.total_score - reference.total_score) >= threshold


def audio_score(a: AudioScore) -> float:
    return float(a.total_score)


def lowbit4k_penalty(is_movie: bool, row_resolution_rank: int, video_bitrate: float, has_good_1080p: bool) -> bool:
    return is_movie and row_resolution_rank >= 2160 and video_bitrate < 12.0 and has_good_1080p


def video_similar(candidate: object, reference: object) -> bool:
    getc = candidate.get if hasattr(candidate, "get") else lambda k, d=None: d
    getr = reference.get if hasattr(reference, "get") else lambda k, d=None: d
    if _safe_int(getc("resolution_rank"), 0) != _safe_int(getr("resolution_rank"), 0):
        return False
    if _safe_int(getc("hdr_rank"), 0) != _safe_int(getr("hdr_rank"), 0):
        return False
    cb = _safe_float(getc("bitrate_mbps_video"), 0.0)
    rb = _safe_float(getr("bitrate_mbps_video"), 0.0)
    return abs(cb - rb) <= 2.0 and (abs(cb - rb) / max(rb, 0.001)) <= 0.10


def video_better(candidate: object, reference: object) -> bool:
    getc = candidate.get if hasattr(candidate, "get") else lambda k, d=None: d
    getr = reference.get if hasattr(reference, "get") else lambda k, d=None: d
    cp = _safe_bool(getc("lowbit4k_penalized"), False)
    rp = _safe_bool(getr("lowbit4k_penalized"), False)
    if cp != rp:
        return not cp and rp
    if video_similar(candidate, reference):
        return False
    cb = _safe_float(getc("bitrate_mbps_video"), 0.0)
    rb = _safe_float(getr("bitrate_mbps_video"), 0.0)
    if abs(cb - rb) > 0.001:
        return cb > rb
    if _safe_int(getc("resolution_rank"), 0) != _safe_int(getr("resolution_rank"), 0):
        return _safe_int(getc("resolution_rank"), 0) > _safe_int(getr("resolution_rank"), 0)
    return _safe_int(getc("hdr_rank"), 0) > _safe_int(getr("hdr_rank"), 0)


@dataclass(frozen=True)
class DuplicateCandidateScore:
    lowbit4k_penalized: bool
    video_bitrate: float
    resolution_rank: int
    hdr_rank: int
    audio_it_score: float
    source_rank: float
    audio_en_score: float
    size_gib: float
    normalized_basename: str
    source_tag: str
    special_source: bool


def candidate_score(row: object) -> DuplicateCandidateScore:
    get = row.get if hasattr(row, "get") else lambda k, d=None: d
    source_tag = str(get("source_tag") or "")
    return DuplicateCandidateScore(
        _safe_bool(get("lowbit4k_penalized", False), False),
        _safe_float(get("bitrate_mbps_video"), 0.0),
        _safe_int(get("resolution_rank"), 0),
        _safe_int(get("hdr_rank"), 0),
        _safe_float(get("audio_it_score"), 0.0),
        _safe_float(get("source_rank"), 0.0),
        _safe_float(get("audio_en_score"), 0.0),
        _safe_float(get("size_gib"), 0.0),
        str(get("normalized_basename") or ""),
        source_tag,
        source_tag in {"full_disc", "dirtyhippie", "ai_upscale"},
    )


def candidate_sort_key(score: DuplicateCandidateScore) -> tuple:
    return (score.lowbit4k_penalized, -score.video_bitrate, -score.resolution_rank, -score.hdr_rank, -score.audio_it_score, -score.source_rank, -score.audio_en_score, -score.size_gib, score.normalized_basename)
