from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from PIL import ImageColor

from hwp_live_values import Rgb


_KOREAN_COLOR_NAMES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "검은색": "black",
        "검정": "black",
        "갈색": "brown",
        "남색": "navy",
        "노란색": "yellow",
        "노랑": "yellow",
        "녹색": "green",
        "보라": "purple",
        "보라색": "purple",
        "분홍": "pink",
        "분홍색": "pink",
        "빨간색": "red",
        "빨강": "red",
        "주황": "orange",
        "주황색": "orange",
        "초록": "green",
        "초록색": "green",
        "파란색": "blue",
        "파랑": "blue",
        "하양": "white",
        "회색": "gray",
        "흰색": "white",
    }
)


def _numeric_rgb(value: str) -> Rgb | None:
    payload = value[4:-1] if value.startswith("rgb(") and value.endswith(")") else value
    pieces = tuple(piece.strip() for piece in payload.split(","))
    if len(pieces) != 3 or any(not piece.isdecimal() for piece in pieces):
        return None
    channels = tuple(int(piece) for piece in pieces)
    if any(channel > 255 for channel in channels):
        return None
    return channels[0], channels[1], channels[2]


def parse_rgb_color(value: str) -> Rgb | None:
    normalized = value.strip().casefold()
    if not normalized:
        return None
    named = _KOREAN_COLOR_NAMES.get(normalized, normalized)
    numeric = _numeric_rgb(named)
    if numeric is not None:
        return numeric
    try:
        parsed = ImageColor.getrgb(named)
    except ValueError:
        return None
    if len(parsed) != 3:
        return None
    return parsed[0], parsed[1], parsed[2]


def canonical_rgb_hex(color: Rgb) -> str:
    return f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"
