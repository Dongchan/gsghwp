from __future__ import annotations

import re
from collections.abc import Iterable
from unicodedata import category, normalize

from hwp_errors import HwpLiveError
from hwp_document_table_profile import resolve_table_geometry, split_wide_table
from hwp_live_contract import (
    DocumentStyle,
    ImageBlock,
    LayoutBlock,
    LayoutPlan,
    ParagraphBlock,
)
from hwp_live_table_contract import TableBlock
from hwp_table_border_inheritance import inherit_conflicting_shared_borders


_ROLE_ALIASES = {
    "body": ("바탕글", "본문", "normal", "bodytext", "body"),
    "table_title": ("표타이틀", "표제목", "표캡션", "tabletitle", "tablecaption"),
    "table_body": ("표내용", "표본문", "tablebody", "tablecontent"),
    "figure_title": (
        "그림타이틀",
        "그림제목",
        "그림캡션",
        "figuretitle",
        "figurecaption",
    ),
}


def _normalized(value: str) -> str:
    return "".join(
        character.casefold()
        for character in normalize("NFKC", value)
        if not character.isspace()
        and not category(character).startswith(("P", "S"))
        and category(character) != "Cf"
    )


def _style_keys(style: DocumentStyle) -> tuple[str, ...]:
    values = (style.name, style.english_name or "")
    return tuple(key for value in values if (key := _normalized(value)))


def _named_style(styles: Iterable[DocumentStyle], name: str) -> int:
    key = _normalized(name)
    for style in styles:
        if key in _style_keys(style):
            return style.style_id
    raise HwpLiveError(f"현재 문서에 스타일 '{name}'이 없습니다")


def _role_style(styles: tuple[DocumentStyle, ...], role: str) -> int | None:
    for alias in _ROLE_ALIASES[role]:
        key = _normalized(alias)
        for style in styles:
            if key in _style_keys(style):
                return style.style_id
    return None


def _named_or_role_style(
    styles: tuple[DocumentStyle, ...],
    name: str,
    role: str,
) -> int | None:
    key = _normalized(name)
    if key in {_normalized(alias) for alias in _ROLE_ALIASES[role]}:
        return _role_style(styles, role)
    return _named_style(styles, name)


def _first_style(*values: int | None) -> int:
    for value in values:
        if value is not None:
            return value
    raise HwpLiveError("현재 문서에서 적용할 스타일을 찾지 못했습니다")


def _heading_shape(value: str) -> str | None:
    text = normalize("NFKC", value).lstrip()
    patterns = (
        ("chapter", r"^제\s*\d+\s*장(?:\s|$)"),
        ("decimal3", r"^\d+\.\d+\.\d+(?:\s|$)"),
        ("decimal2", r"^\d+\.\d+(?:\s|$)"),
        ("korean_dot", r"^[가-힣]\.(?:\s|$)"),
        ("number_paren", r"^\d+\)(?:\s|$)"),
        ("korean_paren", r"^[가-힣]\)(?:\s|$)"),
        ("number_bracket", r"^\(\d+\)(?:\s|$)"),
    )
    return next((name for name, pattern in patterns if re.search(pattern, text)), None)


def _heading_style(styles: tuple[DocumentStyle, ...], shape: str | None) -> int | None:
    wanted = (shape,) if shape is not None else (
        "korean_paren",
        "decimal3",
        "decimal2",
        "korean_dot",
        "number_paren",
        "number_bracket",
        "chapter",
    )
    for candidate_shape in wanted:
        for style in styles:
            if _heading_shape(style.name) == candidate_shape:
                return style.style_id
    return None


def _paragraph_style(
    block: ParagraphBlock,
    styles: tuple[DocumentStyle, ...],
    fallback_style_id: int,
) -> ParagraphBlock:
    if block.style_id is not None:
        return block
    if block.style_name is not None:
        return block.model_copy(
            update={"style_id": _named_style(styles, block.style_name), "style_name": None}
        )
    role = block.style_role
    if role == "auto":
        shape = _heading_shape(block.text)
        resolved = (
            _heading_style(styles, shape)
            if shape is not None
            else _role_style(styles, "body")
        )
    elif role == "heading":
        resolved = _heading_style(styles, _heading_shape(block.text))
    elif role == "table_title":
        resolved = _role_style(styles, "table_title")
    elif role == "figure_title":
        resolved = _role_style(styles, "figure_title")
    else:
        resolved = _role_style(styles, "body")
    return block.model_copy(
        update={"style_id": fallback_style_id if resolved is None else resolved}
    )


def _table_style(
    block: TableBlock,
    styles: tuple[DocumentStyle, ...],
    fallback_style_id: int,
    content_width_mm: float,
) -> TableBlock:
    block = inherit_conflicting_shared_borders(block)
    base_style_id = block.base_style_id
    if base_style_id is None:
        base_style_id = _first_style(
            _named_or_role_style(styles, block.base_style_name, "table_body")
            if block.base_style_name is not None
            else None,
            _role_style(styles, "table_body"),
            _role_style(styles, "body"),
            fallback_style_id,
        )
    caption_style_id = block.caption_style_id
    if block.caption is not None and caption_style_id is None:
        caption_style_id = _first_style(
            _named_or_role_style(styles, block.caption_style_name, "table_title")
            if block.caption_style_name is not None
            else None,
            _role_style(styles, "table_title"),
            _role_style(styles, "body"),
            fallback_style_id,
        )
    styled = block.model_copy(
        update={
            "base_style_id": base_style_id,
            "base_style_name": None,
            "caption_style_id": caption_style_id,
            "caption_style_name": None,
        }
    )
    return resolve_table_geometry(styled, content_width_mm)


def _image_style(
    block: ImageBlock,
    styles: tuple[DocumentStyle, ...],
    fallback_style_id: int,
    content_width_mm: float,
) -> ImageBlock:
    updates: dict[str, object] = {}
    if block.caption is not None and block.caption_style_id is None:
        updates["caption_style_id"] = _first_style(
            _named_or_role_style(styles, block.caption_style_name, "figure_title")
            if block.caption_style_name is not None
            else None,
            _role_style(styles, "figure_title"),
            _role_style(styles, "body"),
            fallback_style_id,
        )
        updates["caption_style_name"] = None
    if block.width_mm > content_width_mm:
        ratio = content_width_mm / block.width_mm
        updates["width_mm"] = content_width_mm
        updates["height_mm"] = block.height_mm * ratio
    return block.model_copy(update=updates) if updates else block


def resolve_layout_style_profile(
    plan: LayoutPlan,
    styles: tuple[DocumentStyle, ...],
    *,
    fallback_style_id: int,
    content_width_mm: float,
) -> LayoutPlan:
    if content_width_mm < 5:
        raise HwpLiveError("현재 문서의 본문 폭을 확인할 수 없습니다")
    blocks: list[LayoutBlock] = []
    for block in plan.blocks:
        if isinstance(block, ParagraphBlock):
            blocks.append(_paragraph_style(block, styles, fallback_style_id))
        elif isinstance(block, TableBlock):
            available = content_width_mm - block.left_margin_mm - block.right_margin_mm
            blocks.extend(
                _table_style(
                    part,
                    styles,
                    fallback_style_id,
                    content_width_mm,
                )
                for part in split_wide_table(block, available)
            )
        elif isinstance(block, ImageBlock):
            blocks.append(
                _image_style(
                    block,
                    styles,
                    fallback_style_id,
                    content_width_mm,
                )
            )
        else:
            blocks.append(block)
    return plan.model_copy(update={"blocks": tuple(blocks)})
