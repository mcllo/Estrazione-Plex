from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re
from typing import Literal

POLICY_VERSION = "v12_normative_general_policy_tv_year_fix_audio_engineering"

RESOLUTION_RANK = {"480": 1, "576": 1, "720": 2, "1080": 3, "1440": 4, "2160": 5, "4k": 5}
HDR_RANK = {"": 0, "sdr": 0, "hdr": 1, "hdr10": 2, "hdr10+": 3, "dolby vision": 4, "dv": 4}

SOURCE_RANK = {
    "full_disc": 9,
    "dirtyhippie": 8,
    "ai_upscale": 8,
    "remux": 7,
    "bluray": 6,
    "web": 5,
    "repack": 4,
    "encode": 3,
}


@dataclass(frozen=True)
class AudioScore:
    codec_family: Literal["lossless_or_master", "lossy", "unknown"]
    broad_tier: Literal["mono", "stereo_or_matrix", "surround", "high_surround", "unknown"]
    channels: float
    bitrate: float


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
    if any(x in low for x in ["aiupscale", "ai_upscale", "upscaled"]):
        return "ai_upscale"
    if cont in {"m2ts", "mpegts", "ts"} or base.endswith(".m2ts") or any(x in low for x in ["full disc", "full_disc", "bdmv", "complete blu"]):
        return "full_disc"
    if "remux" in low:
        return "remux"
    if any(x in low for x in ["web-dl", "webdl", "webrip", "web rip"]):
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
    return AudioScore(audio_codec_family(value), broad_channel_tier(value), _channels_from_quality(value), bitrate_value)


def _tier_num(t: str) -> int:
    return {"unknown": 0, "mono": 1, "stereo_or_matrix": 2, "surround": 3, "high_surround": 4}[t]


def audio_better(candidate: AudioScore, reference: AudioScore, language: str = "it") -> bool:
    # DD+ vs DD guardrail on same channels
    if candidate.channels == reference.channels:
        if candidate.codec_family == reference.codec_family == "lossy":
            return candidate.bitrate > reference.bitrate
    # lossless guardrails
    if candidate.codec_family == "lossless_or_master" and reference.codec_family == "lossy":
        return _tier_num(candidate.broad_tier) + 1 >= _tier_num(reference.broad_tier)
    if candidate.codec_family == "lossy" and reference.codec_family == "lossless_or_master":
        return _tier_num(candidate.broad_tier) - _tier_num(reference.broad_tier) > 1
    # unknown conservative
    if candidate.codec_family == "unknown" and reference.codec_family != "unknown":
        return False
    if reference.codec_family == "unknown" and candidate.codec_family != "unknown":
        return True
    if _tier_num(candidate.broad_tier) != _tier_num(reference.broad_tier):
        return _tier_num(candidate.broad_tier) > _tier_num(reference.broad_tier)
    return candidate.bitrate > reference.bitrate


def audio_score(a: AudioScore) -> tuple[int, int, float, float]:
    fam = {"unknown": 0, "lossy": 1, "lossless_or_master": 2}[a.codec_family]
    return (fam, _tier_num(a.broad_tier), a.channels, a.bitrate)


def lowbit4k_penalty(is_movie: bool, row_resolution_rank: int, video_bitrate: float, has_good_1080p: bool) -> bool:
    return is_movie and row_resolution_rank >= 5 and video_bitrate < 12.0 and has_good_1080p


def basename(path: object) -> str:
    return Path(_safe_text(path)).name
