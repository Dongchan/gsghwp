from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from hwp_errors import HwpLiveError
from hwp_image_fit import fit_image_in_box
from hwp_live_api import HwpControl, LiveHwpApplication
from hwp_live_anchor import require_empty_paragraph
from hwp_live_contract import (
    ImageBlock,
    LayoutPlan,
    ParagraphBlock,
)
from hwp_live_formatting import apply_paragraph_style, apply_text_style
from hwp_live_inspection import inspect_styles
from hwp_live_table import insert_table
from hwp_live_table_contract import TableBlock
from hwp_picture_placement import enforce_picture_size
from hwp_trusted_paths import input_local_image


_TABLE_CAPTION_STYLE_NAMES = frozenset({"표타이틀", "표제목"})
_BASE_STYLE_NAMES = frozenset({"바탕글", "normal"})


def _compact_style_name(name: str) -> str:
    return "".join(character.casefold() for character in name if character.isalnum())


def _resolve_table_caption_style(
    names: tuple[str, ...],
    requested: str | None,
) -> str:
    if requested is not None:
        if requested not in names:
            raise HwpLiveError(f"현재 문서에 표 캡션 스타일 '{requested}'이 없습니다")
        return requested
    exact = tuple(
        name for name in names if _compact_style_name(name) in _TABLE_CAPTION_STYLE_NAMES
    )
    if len(exact) == 1:
        return exact[0]
    fallback = tuple(
        name
        for name in names
        if any(_compact_style_name(name).endswith(alias) for alias in _TABLE_CAPTION_STYLE_NAMES)
    )
    if len(fallback) == 1:
        return fallback[0]
    if not exact and not fallback:
        raise HwpLiveError("현재 문서에서 표타이틀 또는 표 제목 스타일을 찾지 못했습니다")
    raise HwpLiveError("표 캡션 스타일 후보가 여러 개라 caption_style_name 지정이 필요합니다")


def _resolve_base_style(
    names: tuple[str, ...],
    requested: str | None,
) -> str:
    if requested is not None:
        if requested not in names:
            raise HwpLiveError(f"현재 문서에 표 기준 스타일 '{requested}'이 없습니다")
        return requested
    candidates = tuple(
        name for name in names if _compact_style_name(name) in _BASE_STYLE_NAMES
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise HwpLiveError("현재 문서에서 바탕글 스타일을 찾지 못했습니다")
    raise HwpLiveError("표 기준 스타일 후보가 여러 개라 base_style_name 지정이 필요합니다")


def resolve_layout_styles(
    hwp: LiveHwpApplication,
    plan: LayoutPlan,
    guard: Callable[[], None],
) -> LayoutPlan:
    if not any(isinstance(block, TableBlock) for block in plan.blocks):
        return plan
    names = tuple(style.name for style in inspect_styles(hwp, guard).styles)
    blocks = tuple(
        block.model_copy(
            update={
                "base_style_name": _resolve_base_style(names, block.base_style_name),
                **(
                    {
                        "caption_style_name": _resolve_table_caption_style(
                            names,
                            block.caption_style_name,
                        )
                    }
                    if block.caption is not None
                    else {}
                ),
            }
        )
        if isinstance(block, TableBlock)
        else block
        for block in plan.blocks
    )
    return plan.model_copy(update={"blocks": blocks})


def prepare_layout_assets(plan: LayoutPlan) -> dict[Path, Path]:
    assets: dict[Path, Path] = {}
    for block in plan.blocks:
        if isinstance(block, ImageBlock):
            assets[block.path] = input_local_image(block.path)
        elif isinstance(block, TableBlock):
            for row in block.rows:
                for cell in row:
                    if cell.image_path is not None:
                        assets[cell.image_path] = input_local_image(cell.image_path)
    return assets


def _insert_paragraph(
    hwp: LiveHwpApplication,
    block: ParagraphBlock,
    guard: Callable[[], None],
) -> None:
    guard()
    if block.style_id is not None and not hwp.set_style(block.style_id):
        raise HwpLiveError("한컴 문단 스타일을 적용하지 못했습니다")
    guard()
    apply_text_style(
        hwp,
        bold=block.bold,
        font_name=block.font_name,
        font_size_pt=block.font_size_pt,
        text_color=block.text_color,
    )
    guard()
    apply_paragraph_style(
        hwp,
        alignment=block.alignment,
        line_spacing=block.line_spacing_percent,
        before=block.space_before_mm,
        after=block.space_after_mm,
        left_margin=block.left_margin_mm,
        right_margin=block.right_margin_mm,
        indentation=block.indentation_mm,
    )
    guard()
    if not hwp.insert_text(block.text):
        raise HwpLiveError("한컴 문단 텍스트를 삽입하지 못했습니다")
    guard()
    if not hwp.BreakPara():
        raise HwpLiveError("한컴 문단을 삽입하지 못했습니다")
    guard()


def _insert_image(
    hwp: LiveHwpApplication,
    block: ImageBlock,
    assets: dict[Path, Path],
    guard: Callable[[], None],
) -> None:
    guard()
    apply_paragraph_style(hwp, alignment=block.alignment)
    guard()
    control = hwp.insert_picture(str(assets[block.path]))
    guard()
    width, height = fit_image_in_box(
        assets[block.path],
        width_mm=block.width_mm,
        height_mm=block.height_mm,
    )
    enforce_picture_size(
        hwp,
        control,
        width_mm=width,
        height_mm=height,
        guard=guard,
    )
    guard()
    if not hwp.BreakPara():
        raise HwpLiveError("그림 다음 문단을 만들지 못했습니다")
    guard()
    if block.caption is not None:
        if block.caption_style_id is not None and not hwp.set_style(
            block.caption_style_id
        ):
            raise HwpLiveError("한컴 그림 캡션 스타일을 적용하지 못했습니다")
        guard()
        apply_paragraph_style(hwp, alignment="center")
        guard()
        if not hwp.insert_text(block.caption):
            raise HwpLiveError("한컴 그림 캡션 텍스트를 삽입하지 못했습니다")
        guard()
        if not hwp.BreakPara():
            raise HwpLiveError("그림 캡션을 삽입하지 못했습니다")
        guard()


def _insert_page_break(
    hwp: LiveHwpApplication,
    guard: Callable[[], None],
) -> None:
    guard()
    pages_before = hwp.PageCount
    guard()
    position_before = hwp.get_pos()
    guard()
    result = hwp.BreakPage()
    guard()
    pages_after = hwp.PageCount
    guard()
    position_after = hwp.get_pos()
    guard()
    if result or pages_after != pages_before or position_after != position_before:
        return
    guard()
    if not hwp.BreakPara():
        raise HwpLiveError("한컴 쪽 나누기용 문단을 만들지 못했습니다")
    guard()
    pages_before = hwp.PageCount
    guard()
    position_before = hwp.get_pos()
    guard()
    result = hwp.BreakPage()
    guard()
    pages_after = hwp.PageCount
    guard()
    position_after = hwp.get_pos()
    guard()
    if not result and pages_after == pages_before and position_after == position_before:
        raise HwpLiveError("한컴 쪽 나누기를 삽입하지 못했습니다")


def apply_layout(
    hwp: LiveHwpApplication,
    plan: LayoutPlan,
    assets: dict[Path, Path],
    guard: Callable[[], None],
) -> tuple[HwpControl, ...]:
    created: list[HwpControl] = []
    for index, block in enumerate(plan.blocks):
        guard()
        if isinstance(block, ParagraphBlock):
            _insert_paragraph(hwp, block, guard)
        elif isinstance(block, TableBlock):
            created.append(
                insert_table(
                    hwp,
                    block,
                    assets,
                    check_anchor=index != 0,
                    guard=guard,
                )
            )
        elif isinstance(block, ImageBlock):
            _insert_image(hwp, block, assets, guard)
        else:
            _insert_page_break(hwp, guard)
        guard()
    return tuple(created)


def validate_layout_anchor(
    hwp: LiveHwpApplication,
    plan: LayoutPlan,
    guard: Callable[[], None],
) -> None:
    if isinstance(plan.blocks[0], TableBlock):
        require_empty_paragraph(hwp, guard)
