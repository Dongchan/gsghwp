from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_api import Alignment as HwpAlignment
from hwp_live_api import LiveHwpApplication
from hwp_live_values import Alignment, Rgb


_ALIGNMENTS: dict[Alignment, HwpAlignment | None] = {
    "inherit": None,
    "left": "Left",
    "center": "Center",
    "right": "Right",
    "justify": "Justify",
}
_MM_TO_PYHWPX_PARAGRAPH_INPUT = 72.0 / 50.8


def hwp_color(color: Rgb) -> int:
    red, green, blue = color
    return red | green << 8 | blue << 16


def _paragraph_millimeters(value: float | None) -> float | None:
    return None if value is None else value * _MM_TO_PYHWPX_PARAGRAPH_INPUT


def apply_text_style(
    hwp: LiveHwpApplication,
    *,
    bold: bool | None,
    font_name: str | None,
    font_size_pt: float | None,
    text_color: Rgb | None,
) -> None:
    if all(value is None for value in (bold, font_name, font_size_pt, text_color)):
        return
    if not hwp.set_font(
        Bold="" if bold is None else bold,
        FaceName=font_name or "",
        Height="" if font_size_pt is None else font_size_pt,
        TextColor="" if text_color is None else hwp_color(text_color),
    ):
        raise HwpLiveError("한컴 글자 모양을 적용하지 못했습니다")


def apply_paragraph_style(
    hwp: LiveHwpApplication,
    *,
    alignment: Alignment,
    line_spacing: int | None = None,
    before: float | None = None,
    after: float | None = None,
    left_margin: float | None = None,
    right_margin: float | None = None,
    indentation: float | None = None,
) -> None:
    if not hwp.set_para(
        AlignType=_ALIGNMENTS[alignment],
        LineSpacing=line_spacing,
        PrevSpacing=_paragraph_millimeters(before),
        NextSpacing=_paragraph_millimeters(after),
        LeftMargin=_paragraph_millimeters(left_margin),
        RightMargin=_paragraph_millimeters(right_margin),
        Indentation=_paragraph_millimeters(indentation),
    ):
        raise HwpLiveError("한컴 문단 모양을 적용하지 못했습니다")
