from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, assert_never

from hwp_live_native_action_models import (
    BooleanValue,
    EnumerationValue,
    IntegerValue,
    MillimeterValue,
    NativeSetter,
    ParameterActionCommand,
    TextValue,
)
from hwp_live_values import Alignment, Rgb


class CharacterFormatting(Protocol):
    bold: bool | None
    font_name: str | None
    font_size_pt: float | None
    text_color: Rgb | None


@dataclass(frozen=True, slots=True)
class ParagraphFormatting:
    alignment: Alignment
    line_spacing: int | None = None
    before_mm: float | None = None
    after_mm: float | None = None
    left_mm: float | None = None
    right_mm: float | None = None
    indentation_mm: float | None = None


def rgb_value(color: Rgb) -> int:
    red, green, blue = color
    return red | green << 8 | blue << 16


def style_command(style_id: int) -> ParameterActionCommand:
    return ParameterActionCommand(
        action="Style",
        parameter_set="HStyle",
        setters=(NativeSetter("Apply", IntegerValue(style_id)),),
    )


def character_command(
    formatting: CharacterFormatting,
) -> ParameterActionCommand | None:
    setters: list[NativeSetter] = []
    if formatting.bold is not None:
        setters.append(NativeSetter("Bold", BooleanValue(formatting.bold)))
    if formatting.font_size_pt is not None:
        setters.append(
            NativeSetter("Height", IntegerValue(round(formatting.font_size_pt * 100)))
        )
    if formatting.text_color is not None:
        setters.append(
            NativeSetter("TextColor", IntegerValue(rgb_value(formatting.text_color)))
        )
    if formatting.font_name is not None:
        for language in (
            "Hangul",
            "Latin",
            "Hanja",
            "Japanese",
            "Other",
            "Symbol",
            "User",
        ):
            setters.append(
                NativeSetter(f"FaceName{language}", TextValue(formatting.font_name))
            )
            setters.append(NativeSetter(f"FontType{language}", IntegerValue(1)))
    if not setters:
        return None
    return ParameterActionCommand(
        action="CharShape",
        parameter_set="HCharShape",
        setters=tuple(setters),
    )


def _alignment_value(alignment: Alignment) -> str | None:
    match alignment:
        case "inherit":
            return None
        case "left":
            return "Left"
        case "center":
            return "Center"
        case "right":
            return "Right"
        case "justify":
            return "Justify"
    assert_never(alignment)


def paragraph_command(
    formatting: ParagraphFormatting,
) -> ParameterActionCommand | None:
    setters: list[NativeSetter] = []
    alignment = _alignment_value(formatting.alignment)
    if alignment is not None:
        setters.append(
            NativeSetter("AlignType", EnumerationValue("HAlign", alignment))
        )
    if formatting.line_spacing is not None:
        setters.append(
            NativeSetter("LineSpacing", IntegerValue(formatting.line_spacing))
        )
    for name, value in (
        ("PrevSpacing", formatting.before_mm),
        ("NextSpacing", formatting.after_mm),
        ("LeftMargin", formatting.left_mm),
        ("RightMargin", formatting.right_mm),
        ("Indentation", formatting.indentation_mm),
    ):
        if value is not None:
            setters.append(NativeSetter(name, MillimeterValue(value)))
    if not setters:
        return None
    return ParameterActionCommand(
        action="ParagraphShape",
        parameter_set="HParaShape",
        setters=tuple(setters),
    )
