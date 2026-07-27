from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from hwp_errors import HwpLiveError
from hwp_live_contract import (
    ImageBlock,
    LayoutPlan,
    PageBreakBlock,
    ParagraphBlock,
    TableBlock,
)
from hwp_live_native_action_models import IntegerValue, ParameterActionCommand
from hwp_live_values import ContractModel
from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_evidence import prepare_reference_layout
from hwp_reference_layout_geometry import (
    HWPUNITS_PER_INCH,
    MILLIMETERS_PER_INCH,
    SectionPageGeometry,
)
from hwp_reference_layout_native import compile_reference_layout_command
from hwp_reference_layout_patch import ReferenceLayoutPatchBlock


LayoutOverflow = Literal["none", "possible", "definite"]


class LayoutPreflightProblem(ContractModel):
    block_index: int = Field(ge=0)
    block_kind: str
    reason_code: str
    estimated_width_mm: float = Field(ge=0)
    estimated_height_mm: float = Field(ge=0)


class LayoutPreflightResult(ContractModel):
    usable_width_mm: float = Field(gt=0)
    usable_height_mm: float = Field(gt=0)
    estimated_width_mm: float = Field(ge=0)
    estimated_height_mm: float = Field(ge=0)
    overflow: LayoutOverflow
    reason_codes: tuple[str, ...] = ()
    problem_blocks: tuple[LayoutPreflightProblem, ...] = ()
    adjustable_items: tuple[str, ...] = ()
    safe_to_write: bool


@dataclass(frozen=True, slots=True)
class _BlockEstimate:
    width_mm: float
    height_mm: float
    minimum_height_mm: float
    uncertain: bool
    reason_codes: tuple[str, ...]
    adjustable_items: tuple[str, ...]
    definite: bool = False


def _line_count(text: str, width_mm: float, font_size_pt: float) -> int:
    glyph_width_mm = max(0.8, font_size_pt * 0.352_778 * 0.92)
    characters = max(1, math.floor(width_mm / glyph_width_mm))
    return sum(
        max(1, math.ceil(len(line) / characters)) for line in text.splitlines() or ("",)
    )


def _paragraph_estimate(
    block: ParagraphBlock, usable_width_mm: float
) -> _BlockEstimate:
    font_size_pt = 10.0 if block.font_size_pt is None else block.font_size_pt
    width = max(
        1.0,
        usable_width_mm - (block.left_margin_mm or 0) - (block.right_margin_mm or 0),
    )
    line_spacing = (
        1.6 if block.line_spacing_percent is None else block.line_spacing_percent / 100
    )
    line_height = font_size_pt * 0.352_778 * line_spacing
    lines = _line_count(block.text, width, font_size_pt)
    spacing = (block.space_before_mm or 0) + (block.space_after_mm or 0)
    estimated = lines * line_height + spacing
    return _BlockEstimate(
        width_mm=width,
        height_mm=estimated,
        minimum_height_mm=line_height + spacing,
        uncertain=lines > 1 and "\n" not in block.text,
        reason_codes=("TEXT_WRAP_ESTIMATED",) if lines > 1 else (),
        adjustable_items=(
            "reduce_paragraph_spacing",
            "insert_page_break",
        ),
    )


def _table_estimate(block: TableBlock, usable_width_mm: float) -> _BlockEstimate:
    if block.column_widths_mm is None:
        width = usable_width_mm
    else:
        width = (
            sum(block.column_widths_mm) + block.left_margin_mm + block.right_margin_mm
        )
    if block.row_heights_mm is not None:
        height = sum(block.row_heights_mm)
        uncertain = False
        reasons: tuple[str, ...] = ()
    else:
        row_estimates: list[float] = []
        column_width = usable_width_mm / max(1, len(block.rows[0]))
        for row in block.rows:
            cell_heights: list[float] = []
            for cell in row:
                font_size = 10.0 if cell.font_size_pt is None else cell.font_size_pt
                lines = _line_count(cell.text, column_width, font_size)
                line_spacing = (
                    1.6
                    if cell.line_spacing_percent is None
                    else cell.line_spacing_percent / 100
                )
                padding = cell.padding
                vertical_padding = (
                    1.0 if padding is None else padding.top_mm + padding.bottom_mm
                )
                text_height = lines * font_size * 0.352_778 * line_spacing
                image_height = cell.image_height_mm or 0
                cell_heights.append(max(text_height, image_height) + vertical_padding)
            row_estimates.append(max(cell_heights, default=5.0))
        height = sum(row_estimates)
        uncertain = True
        reasons = ("AUTO_FIT_TABLE_HEIGHT",)
    if block.caption is not None:
        height += 7.0
    minimum = (
        height if block.row_heights_mm is not None else max(5.0 * len(block.rows), 1.0)
    )
    adjustable = (
        ("confirm_table_row_heights", "insert_page_break")
        if uncertain
        else ("reduce_table_row_heights", "insert_page_break")
    )
    return _BlockEstimate(
        width_mm=width,
        height_mm=height,
        minimum_height_mm=minimum,
        uncertain=uncertain,
        reason_codes=reasons,
        adjustable_items=adjustable,
    )


def _block_estimate(
    block: ParagraphBlock
    | TableBlock
    | ImageBlock
    | ReferenceLayoutBlock
    | ReferenceLayoutPatchBlock,
    usable_width_mm: float,
    usable_height_mm: float,
    page_geometry: SectionPageGeometry,
    page_number: int,
) -> _BlockEstimate:
    if isinstance(block, ParagraphBlock):
        return _paragraph_estimate(block, usable_width_mm)
    if isinstance(block, TableBlock):
        return _table_estimate(block, usable_width_mm)
    if isinstance(block, ImageBlock):
        caption_height = 7.0 if block.caption is not None else 0.0
        return _BlockEstimate(
            width_mm=block.width_mm,
            height_mm=block.height_mm + caption_height,
            minimum_height_mm=block.height_mm + caption_height,
            uncertain=False,
            reason_codes=(),
            adjustable_items=("reduce_image_height", "insert_page_break"),
        )
    if isinstance(block, ReferenceLayoutPatchBlock):
        return _BlockEstimate(0, 0, 0, False, (), ())
    try:
        command = compile_reference_layout_command(
            prepare_reference_layout(block),
            page_geometry,
            page_number=page_number,
            base_style_id=0,
        )
    except (HwpLiveError, OSError, ValueError):
        return _BlockEstimate(
            usable_width_mm,
            usable_height_mm,
            usable_height_mm,
            False,
            ("REFERENCE_LAYOUT_EXECUTION_REJECTED",),
            ("adjust_reference_layout_geometry",),
            definite=True,
        )
    width = _millimeters(_integer_setter(command, "BodyWidth"))
    height = _millimeters(_integer_setter(command, "BodyHeight"))
    return _BlockEstimate(
        width,
        height,
        height,
        False,
        (),
        ("use_reference_layout_patch",),
    )


def _integer_setter(command: ParameterActionCommand, path: str) -> int:
    value = next(setter.value for setter in command.setters if setter.path == path)
    if not isinstance(value, IntegerValue):
        raise TypeError(f"{path} must be an integer native setter")
    return value.value


def _millimeters(value: int) -> float:
    return value * MILLIMETERS_PER_INCH / HWPUNITS_PER_INCH


def preflight_layout(
    plan: LayoutPlan,
    page_geometry: SectionPageGeometry,
    *,
    page_number: int,
) -> LayoutPreflightResult:
    usable = page_geometry.usable_area(page_number=page_number)
    usable_width = _millimeters(usable.width)
    usable_height = _millimeters(usable.height)
    page_estimated = 0.0
    page_minimum = 0.0
    maximum_estimated = 0.0
    maximum_minimum = 0.0
    maximum_width = 0.0
    uncertain = False
    estimates: list[tuple[int, str, _BlockEstimate]] = []
    reason_codes: list[str] = []
    adjustable: list[str] = []
    layout_page_number = page_number

    for index, block in enumerate(plan.blocks):
        if isinstance(block, PageBreakBlock):
            maximum_estimated = max(maximum_estimated, page_estimated)
            maximum_minimum = max(maximum_minimum, page_minimum)
            page_estimated = 0.0
            page_minimum = 0.0
            layout_page_number += 1
            continue
        estimate = _block_estimate(
            block,
            usable_width,
            usable_height,
            page_geometry,
            layout_page_number,
        )
        estimates.append((index, block.kind, estimate))
        page_estimated += estimate.height_mm
        page_minimum += estimate.minimum_height_mm
        maximum_width = max(maximum_width, estimate.width_mm)
        uncertain = uncertain or estimate.uncertain
        reason_codes.extend(estimate.reason_codes)
        adjustable.extend(estimate.adjustable_items)

    maximum_estimated = max(maximum_estimated, page_estimated)
    maximum_minimum = max(maximum_minimum, page_minimum)
    definite = (
        any(estimate.definite for _, _, estimate in estimates)
        or maximum_minimum > usable_height + 0.1
        or maximum_width > usable_width + 0.1
    )
    possible = uncertain or maximum_estimated > usable_height + 0.1
    overflow: LayoutOverflow = (
        "definite" if definite else "possible" if possible else "none"
    )
    problems = tuple(
        LayoutPreflightProblem(
            block_index=index,
            block_kind=kind,
            reason_code=(
                estimate.reason_codes[0]
                if estimate.reason_codes
                else "CONTRIBUTES_TO_OVERFLOW"
            ),
            estimated_width_mm=round(estimate.width_mm, 3),
            estimated_height_mm=round(estimate.height_mm, 3),
        )
        for index, kind, estimate in estimates
        if overflow != "none"
        and (estimate.height_mm > 0 or estimate.width_mm > usable_width + 0.1)
    )
    if definite:
        reason_codes.append("DEFINITE_PAGE_OVERFLOW")
    elif possible:
        reason_codes.append("LAYOUT_REQUIRES_CONFIRMATION")
    return LayoutPreflightResult(
        usable_width_mm=round(usable_width, 3),
        usable_height_mm=round(usable_height, 3),
        estimated_width_mm=round(maximum_width, 3),
        estimated_height_mm=round(maximum_estimated, 3),
        overflow=overflow,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        problem_blocks=problems,
        adjustable_items=tuple(dict.fromkeys(adjustable)),
        safe_to_write=overflow != "definite",
    )
