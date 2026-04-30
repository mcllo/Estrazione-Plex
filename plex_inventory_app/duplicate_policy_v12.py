from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

POLICY_VERSION = "v12_normative_general_policy_tv_year_fix_audio_engineering"

RESOLUTION_RANK = {"480": 1, "576": 1, "720": 2, "1080": 3, "1440": 4, "2160": 5, "4k": 5}
HDR_RANK = {"": 0, "sdr": 0, "hdr": 1, "hdr10": 2, "hdr10+": 3, "dolby vision": 4, "dv": 4}

SOURCE_PATTERNS = {
    "full_disc": [r"full.?disc", r"bdmv", r"complete.?blu"],
    "dirtyhippie": [r"dirtyhippie"],
    "ai_upscale": [r"ai.?upscale", r"upscaled"],
    "remux": [r"remux"],
    "bluray": [r"blu.?ray", r"bdrip"],
    "web": [r"web.?dl", r"web.?rip"],
}
SOURCE_RANK = {"full_disc": 6, "dirtyhippie": 5, "ai_upscale": 5, "remux": 4, "bluray": 3, "web": 2, "unknown": 1}


@dataclass(frozen=True)
class AudioScore:
    tier: int
    channels: float
    bitrate: float


def normalize_text(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"[^a-z0-9 ]", "", text)


def source_tag_from_path(file_path: str) -> str:
    low = file_path.lower()
    for tag, patterns in SOURCE_PATTERNS.items():
        if any(re.search(p, low) for p in patterns):
            return tag
    return "unknown"


def resolution_rank(value: str | None) -> int:
    text = (value or "").lower()
    for key, rank in RESOLUTION_RANK.items():
        if key in text:
            return rank
    return 0


def hdr_rank(value: str | None) -> int:
    text = (value or "").strip().lower()
    return HDR_RANK.get(text, 0)


def _codec_tier(codec: str) -> int:
    c = codec.lower()
    if any(x in c for x in ["truehd", "dts-hd", "flac", "pcm", "atmos"]):
        return 4
    if "dd+" in c or "eac3" in c:
        return 3
    if "dd" in c or "ac3" in c or "dts" in c:
        return 2
    return 1


def parse_audio_quality(value: str | None, bitrate: float | None) -> AudioScore:
    text = (value or "").lower()
    channels = 0.0
    m = re.search(r"(\d(?:\.\d)?)", text)
    if m:
        channels = float(m.group(1))
    return AudioScore(tier=_codec_tier(text), channels=channels, bitrate=float(bitrate or 0.0))


def lowbit4k_penalty(is_movie: bool, row_resolution_rank: int, video_bitrate: float, has_good_1080p: bool) -> bool:
    return is_movie and row_resolution_rank >= 5 and video_bitrate < 12.0 and has_good_1080p


def basename(path: str) -> str:
    return Path(path or "").name
