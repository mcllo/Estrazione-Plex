from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re
from typing import Literal

POLICY_VERSION = "v12_normative_general_policy_tv_year_fix_audio_engineering"

RESOLUTION_RANK = {"2160": 2160, "4k": 2160, "1080": 1080, "720": 720, "576": 576, "480": 480, "sd": 360}
HDR_RANK = {"": 0, "sdr": 0, "hdr": 1, "hdr10": 2, "hdr10+": 3, "dolby vision": 4, "dv": 4}

SOURCE_RANK = {"full_disc": 9.0, "dirtyhippie": 8.5, "ai_upscale": 8.5, "remux": 8.0, "bluray": 6.5, "web": 6.0, "repack": 5.5, "encode": 5.0}


@dataclass(frozen=True)
class AudioScore:
    codec_key: str
    codec_family: Literal["lossless_or_master", "lossy", "unknown"]
    broad_tier: Literal["mono", "stereo_or_matrix", "surround", "high_surround", "unknown"]
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


def source_tag_from_path(file_path: object, container: object = None) -> str:
    low = _safe_text(file_path).lower()
    base = Path(low).name
    cont = _safe_text(container).lower()
    if any(x in low for x in ["dirtyhippie"]):
        return "dirtyhippie"
    if any(x in low for x in ["aiupscale", "ai_upscale", "ai-enhanced", "ai-upscaled", "ai.upscaled", "upscaled", "rife"]):
        return "ai_upscale"
    if cont in {"m2ts", "mpegts", "ts"} or base.endswith(".m2ts") or any(x in low for x in ["full disc", "full_disc", "bdmv", "complete blu"]):
        return "full_disc"
    if "remux" in low:
        return "remux"
    if any(x in low for x in ["web-dl", "webdl", "webrip", "web rip", "hmax", "amzn", "nf", "dsnp", "uhdrip"]):
        return "web"
    if any(x in low for x in ["bluray", "blu-ray", "bdrip"]):
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
    return HDR_RANK.get(_safe_text(value).strip().lower(), 0)


def audio_codec_family(quality: object) -> Literal["lossless_or_master", "lossy", "unknown"]:
    q = _safe_text(quality).lower()
    if any(x in q for x in ["truehd", "dts-hd", "flac", "pcm", "master", "ma"]):
        return "lossless_or_master"
    if any(x in q for x in ["dd", "dd+", "eac3", "ac3", "aac", "dts"]):
        return "lossy"
    return "unknown"


def _audio_codec_key(quality: object) -> str:
    q = _safe_text(quality).lower()
    if "truehd" in q and "atmos" in q:
        return "truehd_atmos"
    if "dts:x" in q or "dts-x" in q:
        return "dtsx"
    if "truehd" in q:
        return "truehd"
    if "dts-hd" in q or "dts hd" in q or " ma" in f" {q} ":
        return "dtshd_ma"
    if "flac" in q or "lpcm" in q or " pcm" in f" {q} ":
        return "flac_lpcm_pcm"
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


CODEC_SCORE = {"truehd_atmos": 11.0, "dtsx": 10.8, "truehd": 10.5, "dtshd_ma": 10.0, "flac_lpcm_pcm": 9.5, "ddp": 7.5, "dts": 7.0, "dd": 6.8, "opus": 6.0, "aac": 5.8, "mp3": 5.0, "unknown": 0.0}


def _channels_from_quality(quality: object) -> float:
    q = _safe_text(quality).lower()
    m = re.search(r"(\d(?:\.\d)?)", q)
    return float(m.group(1)) if m else 0.0


def broad_channel_tier(quality: object) -> Literal["mono", "stereo_or_matrix", "surround", "high_surround", "unknown"]:
    ch = _channels_from_quality(quality)
    if ch == 0:
        return "unknown"
    if ch <= 1.1:
        return "mono"
    if ch <= 2.1:
        return "stereo_or_matrix"
    if ch <= 5.1:
        return "surround"
    return "high_surround"


def parse_audio_quality(value: object, bitrate: object) -> AudioScore:
    bitrate_value = 0.0
    if bitrate is not None:
        try:
            parsed_bitrate = float(bitrate)
            if not math.isnan(parsed_bitrate):
                bitrate_value = parsed_bitrate
        except (TypeError, ValueError):
            bitrate_value = 0.0
    codec_key = _audio_codec_key(value)
    channels = _channels_from_quality(value)
    codec_score = CODEC_SCORE.get(codec_key, 0.0)
    total_score = codec_score + channels * 0.2 + math.log1p(max(bitrate_value, 0.0))
    return AudioScore(codec_key, audio_codec_family(value), broad_channel_tier(value), channels, bitrate_value, codec_score, total_score)


def _tier_num(t: str) -> int:
    return {"unknown": 0, "mono": 1, "stereo_or_matrix": 2, "surround": 3, "high_surround": 4}[t]


def audio_better(candidate: AudioScore, reference: AudioScore, language: str = "it") -> bool:
    if candidate.codec_key == "unknown" and reference.codec_key != "unknown":
        return False
    if reference.codec_key == "unknown" and candidate.codec_key != "unknown":
        return True
    if candidate.channels == reference.channels and candidate.codec_key == "ddp" and reference.codec_key == "dd" and (candidate.bitrate * 2.0) <= reference.bitrate:
        return False
    # DD+ vs DD guardrail on same channels
    if candidate.channels == reference.channels:
        if candidate.codec_family == reference.codec_family == "lossy":
            return candidate.bitrate > reference.bitrate
    # lossless guardrails
    if candidate.codec_family == "lossless_or_master" and reference.codec_family == "lossy":
        return _tier_num(candidate.broad_tier) + 1 >= _tier_num(reference.broad_tier)
    if candidate.codec_family == "lossy" and reference.codec_family == "lossless_or_master":
        return _tier_num(candidate.broad_tier) - _tier_num(reference.broad_tier) > 1
    if _tier_num(candidate.broad_tier) != _tier_num(reference.broad_tier):
        return _tier_num(candidate.broad_tier) > _tier_num(reference.broad_tier)
    return candidate.bitrate > reference.bitrate


def audio_score(a: AudioScore) -> tuple[int, int, float, float]:
    fam = {"unknown": 0, "lossy": 1, "lossless_or_master": 2}[a.codec_family]
    return (fam, _tier_num(a.broad_tier), a.codec_score, a.total_score)


def lowbit4k_penalty(is_movie: bool, row_resolution_rank: int, video_bitrate: float, has_good_1080p: bool) -> bool:
    return is_movie and row_resolution_rank >= 2160 and video_bitrate < 12.0 and has_good_1080p


@dataclass(frozen=True)
class DuplicateCandidateScore:
    lowbit4k_penalized: bool
    video_bitrate: float
    resolution_rank: int
    hdr_rank: int
    audio_it_score: object
    source_rank: float
    audio_en_score: object
    size_gib: float
    normalized_basename: str
    source_tag: str
    special_source: bool


def candidate_score(row: object) -> DuplicateCandidateScore:
    get = row.get if hasattr(row, "get") else lambda k, d=None: d
    source_tag = str(get("source_tag") or "")
    return DuplicateCandidateScore(
        bool(get("lowbit4k_penalized", False)),
        float(get("bitrate_mbps_video") or 0.0),
        int(get("resolution_rank") or 0),
        int(get("hdr_rank") or 0),
        get("audio_it_score"),
        float(get("source_rank") or 0.0),
        get("audio_en_score"),
        float(get("size_gib") or 0.0),
        str(get("normalized_basename") or ""),
        source_tag,
        source_tag in {"full_disc", "dirtyhippie", "ai_upscale"},
    )


def candidate_sort_key(score: DuplicateCandidateScore) -> tuple:
    return (
        score.lowbit4k_penalized,
        -score.video_bitrate,
        -score.resolution_rank,
        -score.hdr_rank,
        tuple(-x for x in score.audio_it_score),
        -score.source_rank,
        tuple(-x for x in score.audio_en_score),
        -score.size_gib,
        score.normalized_basename,
    )


def basename(path: object) -> str:
    return Path(_safe_text(path)).name
