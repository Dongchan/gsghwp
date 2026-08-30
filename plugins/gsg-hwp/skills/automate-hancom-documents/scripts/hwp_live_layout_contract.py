from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Final, Literal, cast

from pydantic import Field, model_validator

from hwp_image_fit import fit_image_in_box
from hwp_live_paragraph_contract import (
    ParagraphBlock as ParagraphBlock,
    ParagraphRun as ParagraphRun,
)
from hwp_live_table_contract import (
    TableBlock,
    TableCell,
    TableCellStyle,
    apply_cell_style,
)
from hwp_live_values import (
    Alignment,
    ContractModel,
    PlanLeadMm,
    PlanTrailMm,
    reject_plan_lead_input,
    set_plan_lead,
)
from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_patch import ReferenceLayoutPatchBlock


class ImageFrame(ContractModel):
    """An observed table-cell picture frame selected by the model.

    A generic HWP shape frame is intentionally not represented: the current
    bridge cannot report its parent, line, fill, or inner margins reliably.
    """

    container: Literal["table_cell"]
    width_mm: float = Field(ge=1, le=250)
    row_height_mm: float | None = Field(
        default=None,
        ge=1,
        le=250,
        description=(
            "고정 행높이 사용이 관측된 경우에만 지정한다. 실제 높이만 읽고 "
            "고정/자동 여부를 읽지 못했다면 생략한다."
        ),
    )
    alignment: Alignment = Field(
        default="inherit",
        description=(
            "모델이 원문 그림 틀의 페이지 좌표와 본문 폭에서 선택한 앵커 정렬."
        ),
    )
    left_margin_mm: float | None = Field(default=None, ge=0, le=100)
    right_margin_mm: float | None = Field(default=None, ge=0, le=100)
    indentation_mm: float | None = Field(default=None, ge=-100, le=100)
    cell_style: TableCellStyle | None = None


class ImageBlock(ContractModel):
    kind: Literal["image"]
    path: Path
    width_mm: float = Field(ge=1, le=250)
    height_mm: float = Field(ge=1, le=350)
    alignment: Alignment = "inherit"
    container: Literal["paragraph", "table_cell"] = "paragraph"
    plan_lead_mm: PlanLeadMm = None
    plan_trail_mm: PlanTrailMm = None
    caption: str | None = Field(default=None, max_length=2_000)
    caption_style_id: int | None = Field(default=None, ge=0, le=4095)
    caption_style_name: str | None = Field(default=None, min_length=1, max_length=100)
    frame: ImageFrame | None = Field(
        default=None,
        description=(
            "공개 구조 조회에서 확인한 표 셀 그림 틀. 관례를 확인하지 못했으면 "
            "생략하며 그림은 문단에 그대로 들어간다."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def refuse_plan_lead_input(cls, data: object) -> object:
        return reject_plan_lead_input(data)

    @model_validator(mode="after")
    def validate_caption_style(self) -> ImageBlock:
        if self.frame is not None:
            if "container" in self.model_fields_set and self.container != "table_cell":
                raise ValueError("an image frame requires container=table_cell")
            object.__setattr__(self, "container", "table_cell")
        if self.caption is None and (
            self.caption_style_id is not None or self.caption_style_name is not None
        ):
            raise ValueError("caption style requires caption")
        if self.caption_style_id is not None and self.caption_style_name is not None:
            raise ValueError("caption style name and id are mutually exclusive")
        return self


class PageBreakBlock(ContractModel):
    kind: Literal["page_break"]


LayoutBlock = Annotated[
    ParagraphBlock
    | TableBlock
    | ImageBlock
    | PageBreakBlock
    | ReferenceLayoutBlock
    | ReferenceLayoutPatchBlock,
    Field(discriminator="kind"),
]

_BLOCK_KINDS: Final = (
    "paragraph",
    "table",
    "image",
    "page_break",
    "reference_layout",
    "reference_layout_patch",
)
_PARAGRAPH_EXAMPLE: Final = '{"kind": "paragraph", "text": "문단 내용"}'
_BLOCK_KIND_LIST: Final = ", ".join(_BLOCK_KINDS)
_MISSING_KIND_HINT: Final = (
    "모든 레이아웃 블록은 kind로 형식을 밝혀야 합니다. 가능한 값: "
    + _BLOCK_KIND_LIST
    + f". 문단이면 {_PARAGRAPH_EXAMPLE}"
)


class LayoutPlan(ContractModel):
    """실행할 레이아웃 블록의 순서. 최소 형태: {"blocks": [{"kind": "paragraph", "text": "문단 내용"}]}. blocks의 모든 원소는 kind로 자기 형식을 밝혀야 하고, kind가 그 블록이 받는 나머지 항목을 정한다."""  # noqa: E501

    target: Literal["current", "document_end", "after_page"] = Field(
        default="current",
        description=(
            "current는 현재 커서, document_end는 문서 끝, after_page는 page로 "
            "지정한 쪽 다음의 격리된 새 쪽에 삽입합니다"
        ),
    )
    page: int | None = Field(
        default=None,
        ge=1,
        description="target=after_page일 때만 사용하는 1부터 시작하는 기준 쪽 번호",
    )
    replace_selection: bool = Field(
        default=False,
        description="target=current에서 현재 선택 영역을 레이아웃으로 교체할지 여부",
    )
    blocks: tuple[LayoutBlock, ...] = Field(
        min_length=1,
        max_length=100,
        description=(
            "블록의 실행 순서. 원소마다 kind가 필수이며 kind가 형식을 정합니다: "
            "paragraph, table, image, page_break, reference_layout, "
            'reference_layout_patch. 문단이면 {"kind": "paragraph", '
            '"text": "문단 내용"}'
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def explain_missing_block_kind(cls, data: object) -> object:
        """Say which block is wrong and what ``kind`` accepts.

        Pydantic's own discriminator failure is
        ``Unable to extract tag using discriminator 'kind'`` followed by a
        ``Tuple should have at least 1 item`` line for the block it dropped —
        neither says that ``kind`` is required nor what may go in it, and the
        second one reads like a different problem entirely. This only runs
        ahead of the union; the union itself is untouched, so nothing that
        used to be rejected is accepted now.
        """
        if not isinstance(data, dict):
            return data
        mapping = cast("dict[str, object]", data)
        blocks = mapping.get("blocks")
        if not isinstance(blocks, (list, tuple)):
            return mapping
        for index, block in enumerate(cast("Sequence[object]", blocks)):
            if not isinstance(block, dict):
                continue
            kind = cast("dict[str, object]", block).get("kind")
            if kind is None:
                missing = f"blocks[{index}]에 kind가 없습니다."
                raise ValueError(f"{missing} {_MISSING_KIND_HINT}")
            if kind not in _BLOCK_KINDS:
                unknown = f"blocks[{index}]의 kind={kind!r}는 없는 블록 형식입니다."
                raise ValueError(f"{unknown} 가능한 값: {_BLOCK_KIND_LIST}")
        return mapping

    @model_validator(mode="after")
    def validate_size(self) -> LayoutPlan:
        if self.target == "after_page" and self.page is None:
            raise ValueError("after_page target requires page")
        if self.target != "after_page" and self.page is not None:
            raise ValueError("page is only valid with after_page target")
        if self.target != "current" and self.replace_selection:
            raise ValueError(f"{self.target} target cannot replace a selection")
        if self.target != "current" and any(
            isinstance(block, ReferenceLayoutPatchBlock) for block in self.blocks
        ):
            raise ValueError("reference layout patch requires target=current")
        cells = sum(
            len(row)
            for block in self.blocks
            if isinstance(block, TableBlock)
            for row in block.rows
        )
        # 2,500 = 50 x 50. 네이티브가 지원하는 최대 표 하나만큼이다.
        # ReferenceLayoutValidation.cpp:27 이 참조 레이아웃 행·열을 각각 1..50 으로
        # 두고, TableBlock 도 같은 50 을 쓴다(rows max_length=50 / columns 검사).
        # 이전 1,000 은 그 최대 표(2,500 셀)를 제출조차 못 하게 막고 있었다.
        #
        # 명령 예산은 따로 지켜진다. 최악(모든 서식 + 글·그림 + 병합 100)의 50x50
        # 은 약 40,705 개 명령이지만, hwp_live_native_layout.py 의 실행 계획이
        # ActionProtocol.cpp:21 kMaximumCommands(20,000)와
        # LAYOUT_TOPOLOGY_WORK_BUDGET 에 맞춰 쪼갠다(예산 100,000 시절 실측
        # 71회·최대 배치 2,164 — 예산을 낮추면 배치 수만 늘고 호출당 상한은
        # 더 작아진다). 공개 레이아웃 도구는 atomic=False 라서 이 분할 경로를 탄다.
        if cells > 2_500:
            raise ValueError("layout contains more than 2500 expanded table cells")
        return self

    def expand_image_frames(
        self,
        content_width_mm: float | None = None,
        content_height_mm: float | None = None,
    ) -> LayoutPlan:
        expanded: list[LayoutBlock] = []
        for block in self.blocks:
            match block:
                case ImageBlock():
                    scale = min(
                        1.0,
                        (
                            1.0
                            if content_width_mm is None
                            else content_width_mm / block.width_mm
                        ),
                        (
                            1.0
                            if content_height_mm is None
                            else content_height_mm / block.height_mm
                        ),
                    )
                    scaled_width = round(block.width_mm * scale, 4)
                    scaled_height = round(block.height_mm * scale, 4)
                    if block.container == "paragraph":
                        expanded.append(
                            block.model_copy(
                                update={
                                    "width_mm": scaled_width,
                                    "height_mm": scaled_height,
                                    "caption": None,
                                    "caption_style_id": None,
                                    "caption_style_name": None,
                                }
                            )
                        )
                        if block.caption is not None:
                            expanded.append(
                                ParagraphBlock(
                                    kind="paragraph",
                                    text=block.caption,
                                    style_id=block.caption_style_id,
                                    style_name=block.caption_style_name,
                                    style_role="figure_title",
                                    alignment=block.alignment,
                                )
                            )
                        continue
                    frame = block.frame
                    width_limit = scaled_width
                    if frame is not None:
                        width_limit = min(width_limit, frame.width_mm)
                    scale = min(1.0, width_limit / block.width_mm)
                    box_width = block.width_mm * scale
                    box_height = scaled_height
                    fitted_width, fitted_height = fit_image_in_box(
                        block.path,
                        width_mm=box_width,
                        height_mm=box_height,
                    )
                    width = round(fitted_width, 4)
                    height = round(fitted_height, 4)
                    cell = apply_cell_style(
                        TableCell(
                            image_path=block.path,
                            image_width_mm=width,
                            image_height_mm=height,
                            alignment=block.alignment,
                            vertical_alignment="inherit",
                        ),
                        None if frame is None else frame.cell_style,
                    )
                    frame_width = (
                        width
                        if frame is None
                        else min(
                            frame.width_mm,
                            frame.width_mm
                            if content_width_mm is None
                            else content_width_mm,
                        )
                    )
                    table_data: dict[str, object] = {
                        "kind": "table",
                        "border_mode": (
                            "explicit" if cell.borders is not None else "inherit"
                        ),
                        "rows": ((cell,),),
                        "column_widths_mm": (round(frame_width, 4),),
                        "row_heights_mm": (
                            None
                            if frame is None or frame.row_height_mm is None
                            else (frame.row_height_mm,)
                        ),
                        "alignment": ("inherit" if frame is None else frame.alignment),
                    }
                    if frame is not None:
                        for field_name in (
                            "left_margin_mm",
                            "right_margin_mm",
                            "indentation_mm",
                        ):
                            value = cast("float | None", getattr(frame, field_name))
                            if value is not None:
                                table_data[field_name] = value
                    framed = TableBlock.model_validate(table_data)
                    if block.plan_lead_mm is not None:
                        # The image becomes a one-cell table, so its planned
                        # lead has to move with it -- otherwise expanding a
                        # frame drops the placement the plan asked for. It goes
                        # on after validation because the channel refuses
                        # mapping input.
                        framed = set_plan_lead(framed, block.plan_lead_mm)
                    expanded.append(framed)
                    if block.caption is not None:
                        expanded.append(
                            ParagraphBlock(
                                kind="paragraph",
                                text=block.caption,
                                style_id=block.caption_style_id,
                                style_name=block.caption_style_name,
                                style_role="figure_title",
                                alignment=block.alignment,
                            )
                        )
                case (
                    ParagraphBlock()
                    | TableBlock()
                    | PageBreakBlock()
                    | ReferenceLayoutBlock()
                    | ReferenceLayoutPatchBlock()
                ):
                    expanded.append(block)
        return LayoutPlan(
            target=self.target,
            page=self.page,
            replace_selection=self.replace_selection,
            blocks=tuple(expanded),
        )
