from __future__ import annotations

from math import ceil
from typing import final
from unicodedata import category, east_asian_width

from hwp_reference_layout_contract import (
    ReferenceLayoutBlock,
    ReferenceStyle,
    TextAnchor,
)
from hwp_reference_layout_geometry import mm_to_hwpunit


@final
class ReferenceTextHeightError(ValueError):
    available: int
    required: int

    def __init__(self, available: int, required: int) -> None:
        self.available = available
        self.required = required
        message = f"reference-layout text requires {self.required} HWPUNIT"
        message += f"; placement frame has {self.available} HWPUNIT"
        super().__init__(message)


def style_for_text(
    block: ReferenceLayoutBlock,
    anchor: TextAnchor,
) -> tuple[ReferenceStyle | None, ReferenceStyle | None]:
    styles = {style.key: style for style in block.styles}
    region_style = None
    for region in block.style_regions:
        if (
            region.top <= anchor.row < region.bottom
            and region.left <= anchor.column < region.right
        ):
            region_style = styles[region.style_key]
    text_style = (
        styles.get(anchor.style_key)
        if anchor.style_key is not None
        else region_style
    )
    return text_style, region_style


def reference_line_height(style: ReferenceStyle) -> int:
    if style.font_size_pt is None:
        return 0
    font_height = round(style.font_size_pt * 100)
    if style.line_spacing_type == "percent":
        percent = style.line_spacing_percent or 100
        return max(font_height, ceil(font_height * percent / 100))
    spacing = style.line_spacing_hwpunit or 0
    if style.line_spacing_type == "fixed":
        return max(font_height, spacing)
    return font_height + spacing


def _character_width(character: str, style: ReferenceStyle) -> float:
    if style.font_size_pt is None or category(character) in {"Cf", "Mn", "Me"}:
        return 0
    font_height = style.font_size_pt * 100
    if character == "\t":
        em_width = 2.0
    elif east_asian_width(character) in {"W", "F"}:
        em_width = 1.0
    else:
        em_width = 0.5
    glyph_width = font_height * em_width * style.width_ratio_percent / 100
    character_spacing = (
        font_height * max(0, style.letter_spacing_percent) / 100
    )
    return max(0, glyph_width + character_spacing)


def wrapped_line_count(
    text: str,
    style: ReferenceStyle,
    available_width: int | None,
) -> int:
    explicit_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if available_width is None or available_width <= 0:
        return len(explicit_lines)
    line_count = 0
    for line in explicit_lines:
        wrapped_lines = 1
        current_width = 0.0
        pending_space_width = 0.0
        tokens: list[str] = []
        start = 0
        while start < len(line):
            whitespace = line[start].isspace()
            end = start + 1
            while end < len(line) and line[end].isspace() == whitespace:
                end += 1
            tokens.append(line[start:end])
            start = end
        for token in tokens:
            token_width = sum(
                _character_width(character, style) for character in token
            )
            if token.isspace():
                if current_width > 0:
                    pending_space_width += token_width
                continue
            candidate_width = current_width + pending_space_width + token_width
            if candidate_width <= available_width:
                current_width = candidate_width
                pending_space_width = 0
                continue
            if current_width > 0:
                wrapped_lines += 1
                current_width = 0
                pending_space_width = 0
            if token_width <= available_width:
                current_width = token_width
                continue
            for character in token:
                character_width = _character_width(character, style)
                if current_width > 0 and (
                    current_width + character_width > available_width
                ):
                    wrapped_lines += 1
                    current_width = 0
                current_width += character_width
        line_count += wrapped_lines
    return line_count


def minimum_text_height(
    anchor: TextAnchor,
    text_style: ReferenceStyle,
    region_style: ReferenceStyle | None,
    available_width: int | None,
) -> int:
    line_height = reference_line_height(text_style)
    if line_height == 0:
        return 0
    padding = region_style.padding if region_style is not None else None
    horizontal_padding = 0 if padding is None else (
        mm_to_hwpunit(padding.left_mm) + mm_to_hwpunit(padding.right_mm)
    )
    content_width = (
        None
        if available_width is None
        else max(1, available_width - horizontal_padding)
    )
    lines = wrapped_line_count(anchor.text, text_style, content_width)
    explicit_lines = anchor.text.replace("\r\n", "\n").replace("\r", "\n").count("\n") + 1
    paragraphs = explicit_lines if anchor.break_mode == "paragraph" else 1
    paragraph_spacing = paragraphs * (
        mm_to_hwpunit(text_style.paragraph_before_mm)
        + mm_to_hwpunit(text_style.paragraph_after_mm)
    )
    vertical_padding = 0 if padding is None else (
        mm_to_hwpunit(padding.top_mm) + mm_to_hwpunit(padding.bottom_mm)
    )
    return lines * line_height + paragraph_spacing + vertical_padding
