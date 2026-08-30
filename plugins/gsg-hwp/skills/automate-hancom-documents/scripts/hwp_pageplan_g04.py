from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from pydantic import JsonValue
from PIL import Image

from hwp_errors import HwpLiveError
from hwp_image_fit import fit_image_in_box
from hwp_live_contract import (
    ImageBlock,
    LayoutPlan,
    PageBreakBlock,
    ParagraphBlock,
    TableBlock,
    TableCell,
)
from hwp_live_layout_contract import LayoutBlock
from hwp_live_values import set_plan_lead, set_plan_trail
from hwp_layout_preflight import (
    paragraph_flow_height_mm,
    table_row_content_heights_mm,
)
from hwp_pageplan_assets import (
    SourceRegistry,
    TableSourceRecord,
    TextSourceRecord,
    verify_source_registry,
)
from hwp_pageplan_contract import (
    ImagePlanBlock,
    MmBox,
    PageBreakPlanBlock,
    PagePlan,
    PagePlanPage,
    ParagraphPlanBlock,
    TablePlanBlock,
    canonical_json_bytes,
    canonical_page_plan_sha256,
    sha256_bytes,
    verify_page_plan_sources,
)
from hwp_pageplan_g03_contract import (
    G03Accepted,
    accepted_hash_payload,
    canonical_accepted_evidence_sha256,
)
from hwp_pageplan_g04_contract import (
    G04EmbeddedAssetEvidence,
    G04Error,
    G04ErrorCode,
    G04SourceEvidence,
)


LayoutParagraphRole = Literal[
    "auto",
    "body",
    "heading",
    "table_title",
    "figure_title",
]


_LAYOUT_PARAGRAPH_ROLES: Final = frozenset(
    {"auto", "body", "heading", "table_title", "figure_title"}
)
_TABLE_ROLES: Final = frozenset(
    {"body", "table", "table_body", "table_title", "heading"}
)
_BINARY_TAG: Final = re.compile(
    r"<BINDATA\b(?P<attributes>[^>]*)>(?P<data>.*?)</BINDATA>",
    re.IGNORECASE | re.DOTALL,
)
_ELEMENT_ATTRIBUTES: Final = re.compile(
    r"(?P<name>[A-Za-z_:][A-Za-z0-9_.:-]*)\s*=\s*\"(?P<value>[^\"]*)\""
)
_PICTURE_TAG: Final = re.compile(
    r"<PICTURE\b[^>]*>(?P<body>.*?)</PICTURE>",
    re.IGNORECASE | re.DOTALL,
)
_IMAGE_TAG: Final = re.compile(r"<IMAGE\b(?P<attributes>[^>]*)/?>", re.IGNORECASE)
_SHAPE_SIZE: Final = re.compile(
    r"<SHAPEOBJECT\b.*?<SIZE\b(?P<attributes>[^>]*)/?>",
    re.IGNORECASE | re.DOTALL,
)
# Below this a "displacement" is the arithmetic's own rounding, not the flow
# actually pushing a block down.
_PLACEMENT_TOLERANCE_MM: Final = 0.01
# What a figure caption adds under its picture. The preflight has used this
# same 7.0 for the caption it cannot measure since before this lane; the
# lowering has to agree with it or the two disagree about where the next block
# starts.
CAPTION_FLOW_HEIGHT_MM: Final = 7.0
# A table inserted as a character sits inside a paragraph line that is taller
# than the table, evenly above and below it. MEASURED on Hangul 2024 at 200dpi,
# pinned rows and the line pinned to 100%, tables read off the render as ink
# bands and the following picture read by exact colour:
#
#   planned table  rows  measured top   own offset   next block gains
#      45.0mm        3      45.97mm       +0.97          --
#      60.0mm        2      60.96mm       +0.96        +2.03mm
#     150.0mm        3     151.00mm       +1.00        +2.06mm
#      70.0mm        2      70.99mm       +0.99        +2.05mm
#     120.0mm        2     122.94mm         --         +1.99mm
#
# The gain does not move with the table's height (15 / 20 / 40mm) or its row
# count, and it is twice the top offset, so it is one constant applied on each
# side. Pictures measured in the same runs land at +0.09..+0.17mm, so this is
# the table's own, not the placement's.
TABLE_FLOW_LEADING_MM: Final = 1.0


class G04PreparationError(ValueError):
    code: str
    message: str
    block_id: str | None
    slot_id: int | None
    source_ref: str | None
    expected: object
    actual: object

    def __init__(
        self,
        code: str,
        message: str,
        *,
        block_id: str | None = None,
        slot_id: int | None = None,
        source_ref: str | None = None,
        expected: object = None,
        actual: object = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.block_id = block_id
        self.slot_id = slot_id
        self.source_ref = source_ref
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True, slots=True)
class G04ImageExpectation:
    block_id: str
    slot_id: int
    source_ref: str
    path: Path
    byte_sha256: str
    width_mm: float
    height_mm: float
    source_width_px: int
    source_height_px: int

    @property
    def aspect_ratio(self) -> float:
        return self.source_width_px / self.source_height_px


@dataclass(frozen=True, slots=True)
class G04LoweredPagePlan:
    page_plan: PagePlan
    source_registry: SourceRegistry
    layout: LayoutPlan
    images: tuple[G04ImageExpectation, ...]
    plan_sha256: str
    placements: tuple[G04Placement, ...] = ()

    @property
    def predicted_bottom_mm(self) -> float:
        """Where the lowering expects the last block to end on the page."""
        return max(
            (placement.placed_bottom_mm for placement in self.placements),
            default=self.page_plan.pages[0].body_box.top_mm,
        )

    @property
    def displaced(self) -> tuple[G04Placement, ...]:
        """Blocks the flow had to push below their planned top.

        Non-empty means the plan asked for boxes that overlap, or for content
        taller than the box it was given: the flow cannot run backward, so
        those blocks and everything after them sit lower than planned.
        """
        return tuple(
            placement
            for placement in self.placements
            if placement.displacement_mm > _PLACEMENT_TOLERANCE_MM
        )


@dataclass(frozen=True, slots=True)
class HwpmlBinary:
    bin_item: int
    raw: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class HwpmlPicture:
    bin_item: int
    width_hwpunit: int
    height_hwpunit: int


@dataclass(frozen=True, slots=True)
class HwpmlImageInspection:
    binaries: tuple[HwpmlBinary, ...]
    pictures: tuple[HwpmlPicture, ...]


@dataclass(frozen=True, slots=True)
class G04EmbeddedProof:
    evidence: tuple[G04EmbeddedAssetEvidence, ...]
    complete: bool


def _attributes(value: str) -> dict[str, str]:
    return {
        match.group("name"): match.group("value")
        for match in _ELEMENT_ATTRIBUTES.finditer(value)
    }


def _integer_attribute(attributes: dict[str, str], name: str) -> int | None:
    try:
        value = int(attributes[name])
    except (KeyError, ValueError):
        return None
    return value if value >= 0 else None


def _decode_bindata(attributes: str, data: str) -> HwpmlBinary | None:
    parsed = _attributes(attributes)
    bin_item = _integer_attribute(parsed, "Id")
    if bin_item is None:
        return None
    compact = re.sub(r"\s+", "", data)
    try:
        raw = base64.b64decode(compact, validate=True)
    except (ValueError, binascii.Error):
        return None
    return HwpmlBinary(bin_item, raw, hashlib.sha256(raw).hexdigest())


def inspect_hwpml_images(content: str) -> HwpmlImageInspection:
    binaries = tuple(
        item
        for match in _BINARY_TAG.finditer(content)
        if (item := _decode_bindata(match.group("attributes"), match.group("data")))
        is not None
    )
    pictures: list[HwpmlPicture] = []
    for picture_match in _PICTURE_TAG.finditer(content):
        body = picture_match.group("body")
        image_match = _IMAGE_TAG.search(body)
        if image_match is None:
            continue
        image_attributes = _attributes(image_match.group("attributes"))
        bin_item = _integer_attribute(image_attributes, "BinItem")
        if bin_item is None:
            continue
        size_match = _SHAPE_SIZE.search(body)
        if size_match is None:
            continue
        size_attributes = _attributes(size_match.group("attributes"))
        width = _integer_attribute(size_attributes, "Width")
        height = _integer_attribute(size_attributes, "Height")
        if width is None or height is None or width <= 0 or height <= 0:
            continue
        pictures.append(HwpmlPicture(bin_item, width, height))
    return HwpmlImageInspection(binaries, tuple(pictures))


def _paragraph_role(block: ParagraphPlanBlock) -> LayoutParagraphRole:
    role = block.style_role
    if role in _LAYOUT_PARAGRAPH_ROLES:
        return cast(LayoutParagraphRole, role)
    if block.semantic_role == "caption":
        return "figure_title"
    if block.semantic_role == "heading":
        return "heading"
    if block.semantic_role == "table_title":
        return "table_title"
    return "body"


def _table_style_role(block: TablePlanBlock) -> str:
    role = block.style_role
    if role not in _TABLE_ROLES:
        raise G04PreparationError(
            "UNSUPPORTED_LAYOUT_VOCABULARY",
            f"표 블록의 style_role은 G04 표 어휘에 없습니다: {role}",
            block_id=block.block_id,
        )
    return "table_body"


def source_evidence_for_plan(
    page_plan: PagePlan,
    source_registry: SourceRegistry,
) -> tuple[G04SourceEvidence, ...]:
    refs: list[str] = []
    for page in page_plan.pages:
        for block in page.blocks:
            if isinstance(block, ParagraphPlanBlock | TablePlanBlock):
                refs.append(block.source_ref)
            elif isinstance(block, ImagePlanBlock):
                slot = page_plan.slot_map.require(block.slot_id)
                refs.append(slot.source or "")
                if block.caption_source_ref is not None:
                    refs.append(block.caption_source_ref)
    result: list[G04SourceEvidence] = []
    for source_ref in dict.fromkeys(refs):
        source = source_registry.require(source_ref)
        if isinstance(source, TextSourceRecord):
            result.append(
                G04SourceEvidence(
                    source_ref=source.source_ref,
                    source_kind="text",
                    expected_sha256=source.content_sha256,
                    verified_sha256=source.content_sha256,
                )
            )
        elif isinstance(source, TableSourceRecord):
            result.append(
                G04SourceEvidence(
                    source_ref=source.source_ref,
                    source_kind="table",
                    expected_sha256=source.content_sha256,
                    verified_sha256=source.content_sha256,
                )
            )
        else:
            result.append(
                G04SourceEvidence(
                    source_ref=source.source_ref,
                    source_kind="image",
                    expected_sha256=source.byte_sha256,
                    verified_sha256=source.byte_sha256,
                    final_insertable=source.final_insertable,
                )
            )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class G04Placement:
    """Where one plan block will actually land once the flow runs.

    ``lead_mm`` is what goes on the block; the other two are what the lowering
    predicts will happen, and they are what the next block's lead is measured
    from.
    """

    block_id: str
    planned_top_mm: float
    lead_mm: float
    placed_top_mm: float
    realised_height_mm: float

    @property
    def placed_bottom_mm(self) -> float:
        return self.placed_top_mm + self.realised_height_mm

    @property
    def displacement_mm(self) -> float:
        return round(self.placed_top_mm - self.planned_top_mm, 4)


PositionedPlanBlock = ParagraphPlanBlock | TablePlanBlock | ImagePlanBlock


def plan_block_order(page: PagePlanPage) -> tuple[PositionedPlanBlock, ...]:
    """Plan blocks in the order the flow has to emit them: top first.

    Declaration order carries no vertical meaning in an absolute plan -- a
    block's position is its ``box.top_mm``. Emitting a block declared first but
    positioned last would make every later block chase a cursor that is already
    past them (MEASURED: a plan declaring 150 / 60 / 220mm put the 60.0mm block
    at 170.18mm, +110mm). Sorting is the arithmetic being right, not a guard:
    nothing is rejected and no block is dropped.
    """
    positioned: list[tuple[int, PositionedPlanBlock]] = []
    for index, block in enumerate(page.blocks):
        if isinstance(block, PageBreakPlanBlock):
            # One PagePlanPage is one page, so ordering by top is total. An
            # explicit break would make "top" mean two different origins in one
            # list and every number after it wrong; G04 already refuses these
            # up front (MULTI_PAGE_REQUIRES_G05), so this only fires if a
            # future multi-page caller reuses this helper without splitting the
            # page first. Saying so beats returning arithmetic that is silently
            # off by a page.
            message = "page-break blocks must be split into their own"
            raise ValueError(f"{message} PagePlanPage before ordering")
        positioned.append((index, block))
    positioned.sort(key=lambda item: (item[1].box.top_mm, item[0]))
    return tuple(block for _, block in positioned)


def _advance(
    cursor_mm: float,
    block_id: str,
    planned_top_mm: float,
    realised_height_mm: float,
    *,
    leading_mm: float = 0.0,
) -> tuple[G04Placement, float]:
    """Place one block against the flow cursor and move the cursor past it.

    ``placed_top`` is at least ``cursor + leading``: flow cannot run backward,
    so a block whose plan box overlaps the one above it is pushed down to where
    the flow actually is. The next lead is then measured from where this block
    really ends, which is what stops one overlap from displacing everything
    under it (MEASURED before this: a third, non-overlapping block moved +15mm).

    ``leading_mm`` is the padding the flow puts on each side of the object
    inside its own paragraph -- zero for a picture, ``TABLE_FLOW_LEADING_MM``
    for a table. It comes off the lead so the object itself lands on the plan,
    and it is added back below so the next block is measured from the bottom of
    the paragraph rather than the bottom of the object.
    """
    placed_top = max(planned_top_mm, cursor_mm + leading_mm)
    placement = G04Placement(
        block_id=block_id,
        planned_top_mm=planned_top_mm,
        lead_mm=round(placed_top - leading_mm - cursor_mm, 4),
        placed_top_mm=placed_top,
        realised_height_mm=realised_height_mm,
    )
    return placement, placement.placed_bottom_mm + leading_mm


def plan_vertical_leads(page: PagePlanPage) -> dict[str, float]:
    """The planned gap ahead of each block, ignoring what the flow will do.

    This is the geometry of the plan alone: it assumes every block occupies
    exactly its own box. ``lower_page_plan`` does not use it -- the real
    lowering advances the cursor by each block's *realised* height, because a
    paragraph and a table do not fill their planned boxes. No production code
    reads it today (verified by search); it stays only as the plan's own
    statement of intent, pinned by its tests.
    """
    leads: dict[str, float] = {}
    cursor = page.body_box.top_mm
    for block in plan_block_order(page):
        placement, cursor = _advance(
            cursor,
            block.block_id,
            block.box.top_mm,
            block.box.height_mm,
        )
        leads[block.block_id] = placement.lead_mm
    return leads


def table_row_heights_mm(
    box_height_mm: float,
    row_minimums_mm: tuple[float, ...],
) -> tuple[float, ...] | None:
    """Split a table's planned box height across its rows.

    A plan gives the table one box and no per-row detail, so the height is
    shared equally. Pinning it is what makes the table occupy the height the
    plan reserved: left to auto-fit, a 25.0mm planned table rendered 15.5mm and
    every block under it climbed by the difference (MEASURED -6.24 / -15.72mm
    on a mixed plan).

    A row never goes below what its own content needs -- HWP grows it back
    anyway (MEASURED: five rows pinned to 4.0mm rendered 22.73mm, not 20.0),
    and a pin the engine will not honour would make the lowering predict a
    height that never happens. Such a table ends up taller than its plan box,
    which ``_advance`` then reports as a displacement instead of hiding it.
    """
    if not row_minimums_mm:
        return None
    share = box_height_mm / len(row_minimums_mm)
    heights = tuple(
        round(max(share, minimum), 4) for minimum in row_minimums_mm
    )
    if any(height < 1.0 or height > 250.0 for height in heights):
        return None
    return heights


def _lower_table(
    block: TablePlanBlock,
    source: TableSourceRecord,
    body_left_mm: float,
) -> TableBlock:
    columns = len(source.rows[0])
    cells = tuple(
        tuple(TableCell(text=value) for value in row) for row in source.rows
    )
    unpinned = TableBlock(
        kind="table",
        rows=cells,
        base_style_name=_table_style_role(block),
        target_width_mm=block.box.width_mm,
        column_width_weights=tuple(1.0 for _ in range(columns)),
        left_margin_mm=max(0.0, block.box.left_mm - body_left_mm),
    )
    heights = table_row_heights_mm(
        block.box.height_mm,
        table_row_content_heights_mm(unpinned, block.box.width_mm),
    )
    if heights is None:
        return unpinned
    return unpinned.model_copy(update={"row_heights_mm": heights})


def lower_page_plan(
    page_plan: PagePlan,
    source_registry: SourceRegistry,
) -> G04LoweredPagePlan:
    page = page_plan.pages[0]
    body = page.body_box
    cursor = body.top_mm
    placements: list[G04Placement] = []
    lowered: list[LayoutBlock] = [PageBreakBlock(kind="page_break")]
    images: list[G04ImageExpectation] = []
    for block in page.blocks:
        if isinstance(block, PageBreakPlanBlock):
            raise G04PreparationError(
                "MULTI_PAGE_REQUIRES_G05",
                "one-page G04 plans cannot contain an explicit page break",
                block_id=block.block_id,
            )
    for block in plan_block_order(page):
        if isinstance(block, ParagraphPlanBlock):
            source = source_registry.require_text(block.source_ref)
            if not source.content:
                raise G04PreparationError(
                    "UNSUPPORTED_LAYOUT_VOCABULARY",
                    "G04 cannot lower an empty paragraph source",
                    block_id=block.block_id,
                    source_ref=block.source_ref,
                )
            paragraph = ParagraphBlock(
                kind="paragraph",
                text=source.content,
                style_role=_paragraph_role(block),
                preserve_source_text=True,
            )
            # A paragraph is as tall as its own text; it cannot be pinned to
            # its plan box the way a picture or a table can. Advancing by the
            # estimate the preflight already uses keeps the two in one
            # coordinate system, and keeps the error to the estimator's own
            # (MEASURED 5.64mm estimated vs 5.70mm rendered for one 10pt line)
            # instead of the whole box/content difference.
            placement, cursor = _advance(
                cursor,
                block.block_id,
                block.box.top_mm,
                paragraph_flow_height_mm(paragraph, block.box.width_mm),
            )
            placements.append(placement)
            lowered.append(set_plan_lead(paragraph, placement.lead_mm))
            continue
        if isinstance(block, TablePlanBlock):
            source = source_registry.require_table(block.source_ref)
            table = _lower_table(block, source, body.left_mm)
            placement, cursor = _advance(
                cursor,
                block.block_id,
                block.box.top_mm,
                sum(table.row_heights_mm)
                if table.row_heights_mm is not None
                else sum(table_row_content_heights_mm(table, block.box.width_mm)),
                leading_mm=TABLE_FLOW_LEADING_MM,
            )
            placements.append(placement)
            # A table's own paragraph cannot hold the gap: Hangul moves the
            # whole paragraph to the next page and the one-page G04 refuses its
            # own layout (MEASURED: 50mm lands at +0.00mm, 70mm adds a page,
            # 148mm adds two). The gap is the same amount of space either way,
            # so it goes on the block above, where a picture held 148mm and a
            # paragraph 175mm without moving.
            #
            # The table still gets an explicit zero. Leaving its space-before
            # unwritten lets the table style's own space-before apply on top of
            # the gap, which put the block under the table 2.18mm low
            # (MEASURED) instead of 0.15mm.
            carrier = lowered[-1] if len(lowered) > 1 else None
            if carrier is None:
                lowered.append(set_plan_lead(table, placement.lead_mm))
            else:
                lowered[-1] = set_plan_trail(carrier, placement.lead_mm)
                lowered.append(set_plan_lead(table, 0.0))
            continue
        if block.kind == "image":
            slot = page_plan.slot_map.require(block.slot_id)
            source = source_registry.require_image(slot.source or "")
            try:
                with Image.open(source.path) as image:
                    width_px, height_px = image.size
            except (OSError, ValueError) as error:
                raise G04PreparationError(
                    "SOURCE_HASH_MISMATCH",
                    f"원본 그림의 크기를 읽지 못했습니다: {source.source_ref}",
                    block_id=block.block_id,
                    slot_id=block.slot_id,
                    source_ref=source.source_ref,
                ) from error
            width_mm, height_mm = fit_image_in_box(
                source.path,
                width_mm=block.box.width_mm,
                height_mm=block.box.height_mm,
            )
            caption: str | None = None
            if block.caption_source_ref is not None:
                caption = source_registry.require_text(
                    block.caption_source_ref
                ).content
            picture = ImageBlock(
                kind="image",
                path=source.path,
                width_mm=width_mm,
                height_mm=height_mm,
                caption=caption,
            )
            # "contain" fitting means the picture is usually shorter than its
            # plan box, and the flow gives it exactly its fitted height once
            # the line is pinned -- so the fitted height, not the box height,
            # is what the next block is measured from.
            placement, cursor = _advance(
                cursor,
                block.block_id,
                block.box.top_mm,
                height_mm + (CAPTION_FLOW_HEIGHT_MM if caption is not None else 0.0),
            )
            placements.append(placement)
            lowered.append(set_plan_lead(picture, placement.lead_mm))
            images.append(
                G04ImageExpectation(
                    block_id=block.block_id,
                    slot_id=block.slot_id,
                    source_ref=source.source_ref,
                    path=source.path,
                    byte_sha256=source.byte_sha256,
                    width_mm=width_mm,
                    height_mm=height_mm,
                    source_width_px=width_px,
                    source_height_px=height_px,
                )
            )
            continue
        raise G04PreparationError(
            "UNSUPPORTED_LAYOUT_VOCABULARY",
            f"지원하지 않는 G04 블록입니다: {type(block).__name__}",
        )
    if len(lowered) > 100:
        raise G04PreparationError(
            "UNSUPPORTED_LAYOUT_VOCABULARY",
            "G04 원자 요청은 page-break를 포함해 100개 블록까지 지원합니다",
            expected=100,
            actual=len(lowered),
        )
    try:
        layout = LayoutPlan(
            target="document_end",
            blocks=tuple(lowered),
        )
    except ValueError as error:
        raise G04PreparationError(
            "UNSUPPORTED_LAYOUT_VOCABULARY",
            f"PagePlan을 원자 LayoutPlan으로 낮추지 못했습니다: {error}",
        ) from error
    return G04LoweredPagePlan(
        page_plan=page_plan,
        source_registry=source_registry,
        layout=layout,
        images=tuple(images),
        plan_sha256=canonical_page_plan_sha256(page_plan),
        placements=tuple(placements),
    )


def g04_overflow_hint(
    lowered: G04LoweredPagePlan,
    body: MmBox,
    *,
    expected_pages: int,
    actual_pages: int,
) -> str:
    """Say how much vertical room the plan is short of, in millimetres.

    "It did not end on exactly one page" tells a caller nothing they can act
    on. The lowering already predicts where each block lands, so the deficit
    against the body box is available for free, and a plan can be corrected
    against a number instead of by trial.
    """
    predicted = lowered.predicted_bottom_mm
    short = round(predicted - body.bottom_mm, 2)
    pages = f" (쪽 수 {expected_pages} 기대, {actual_pages} 실제)."
    parts = [
        pages,
        f" 계획된 내용이 본문 바닥({round(body.bottom_mm, 2)}mm) 아래 ",
        f"{round(predicted, 2)}mm 까지 내려갑니다",
    ]
    if short > 0:
        parts.append(f". 최소 {short}mm 를 줄이거나 위로 올려야 합니다")
    else:
        parts.append(
            ". 계획 자체는 본문 안이므로 블록 내용이 계획한 상자보다 커졌습니다"
        )
    displaced = lowered.displaced
    if displaced:
        names = ", ".join(
            f"{placement.block_id}(+{placement.displacement_mm}mm)"
            for placement in displaced[:3]
        )
        parts.append(f". 계획보다 아래로 밀린 블록: {names}")
    return "".join(parts)


def _g03_result_sha256(result: G03Accepted) -> str:
    return sha256_bytes(canonical_json_bytes(accepted_hash_payload(result)))


def validate_g03_result(
    result: G03Accepted,
    source_registry: SourceRegistry,
    document_selector: str,
) -> None:
    if result.canonical_evidence_sha256 != canonical_accepted_evidence_sha256(
        result
    ) or result.deterministic_result_sha256 != _g03_result_sha256(result):
        raise G04PreparationError(
            "G03_PROVENANCE_INVALID",
            "G03 compiled result seal does not match its payload",
        )
    if result.grounding.document_selector != document_selector:
        raise G04PreparationError(
            "DOCUMENT_SELECTOR_MISMATCH",
            "G03 grounding selector and G04 target selector differ",
            expected=result.grounding.document_selector,
            actual=document_selector,
        )
    if result.plan.source_manifest_sha256 != result.source_manifest_sha256:
        raise G04PreparationError(
            "G03_PROVENANCE_INVALID",
            "G03 plan and result source manifests differ",
        )
    if result.plan.style_roles_sha256 != result.style_roles_sha256:
        raise G04PreparationError(
            "G03_PROVENANCE_INVALID",
            "G03 plan and result style-role hashes differ",
        )
    if result.source_manifest_sha256 != source_registry.manifest_sha256:
        raise G04PreparationError(
            "SOURCE_HASH_MISMATCH",
            "G04 source registry does not match the compiled G03 manifest",
            expected=result.source_manifest_sha256,
            actual=source_registry.manifest_sha256,
        )
    if result.plan.candidate != "A" or len(result.plan.pages) != 1:
        raise G04PreparationError(
            "MULTI_PAGE_REQUIRES_G05",
            "G04 applies only candidate A one-page plans; use G05 for candidate B",
            expected="A/1",
            actual=f"{result.plan.candidate}/{len(result.plan.pages)}",
        )
    if any(
        isinstance(block, PageBreakPlanBlock)
        for block in result.plan.pages[0].blocks
    ):
        raise G04PreparationError(
            "MULTI_PAGE_REQUIRES_G05",
            "an explicit PagePlan page break requires G05",
        )
    try:
        verify_page_plan_sources(
            result.plan,
            source_registry,
            require_final_assets=True,
        )
    except ValueError as error:
        message = str(error)
        code = (
            "NON_FINAL_INSERTABLE_ASSET"
            if "final-insertable" in message or "final_insertable" in message
            else "SOURCE_HASH_MISMATCH"
        )
        raise G04PreparationError(code, message) from error
    try:
        verify_source_registry(source_registry, require_final_assets=True)
    except ValueError as error:
        message = str(error)
        code = (
            "NON_FINAL_INSERTABLE_ASSET"
            if "final-insertable" in message or "final_insertable" in message
            else "SOURCE_HASH_MISMATCH"
        )
        raise G04PreparationError(code, message) from error


def prepare_g04_application(
    result: G03Accepted,
    source_registry: SourceRegistry,
    document_selector: str,
) -> G04LoweredPagePlan:
    validate_g03_result(result, source_registry, document_selector)
    try:
        return lower_page_plan(result.plan, source_registry)
    except G04PreparationError:
        raise
    except (OSError, ValueError) as error:
        raise G04PreparationError(
            "UNSUPPORTED_LAYOUT_VOCABULARY",
            f"G04 lowering failed: {error}",
        ) from error


def g04_error_from_exception(error: G04PreparationError) -> G04Error:
    # Pydantic's JsonValue excludes arbitrary objects, so only pass values that
    # are naturally serializable in the public receipt.
    expected = (
        cast(JsonValue, error.expected)
        if isinstance(error.expected, (str, int, float, bool, list, dict, type(None)))
        else None
    )
    actual = (
        cast(JsonValue, error.actual)
        if isinstance(error.actual, (str, int, float, bool, list, dict, type(None)))
        else None
    )
    return G04Error(
        code=cast(G04ErrorCode, error.code),
        message=error.message,
        block_id=error.block_id,
        slot_id=error.slot_id,
        source_ref=error.source_ref,
        expected=expected,
        actual=actual,
    )


def verify_embedded_images(
    before_content: str,
    after_content: str,
    images: Iterable[G04ImageExpectation],
    *,
    ratio_tolerance: float = 0.01,
) -> G04EmbeddedProof:
    before = inspect_hwpml_images(before_content)
    after = inspect_hwpml_images(after_content)
    before_ids = {item.bin_item for item in before.binaries}
    before_counts = Counter(item.sha256 for item in before.binaries)
    evidence: list[G04EmbeddedAssetEvidence] = []
    complete = True
    for expected in images:
        matching = tuple(
            item
            for item in after.binaries
            if item.sha256 == expected.byte_sha256
            and item.bin_item not in before_ids
        )
        embedded_hashes = tuple(item.sha256 for item in matching)
        new_ids = tuple(item.bin_item for item in matching)
        byte_fidelity = bool(matching) and (
            Counter(item.sha256 for item in after.binaries)[expected.byte_sha256]
            > before_counts[expected.byte_sha256]
        )
        observed_ratios: list[float] = []
        for item in matching:
            for picture in after.pictures:
                if picture.bin_item == item.bin_item:
                    observed_ratios.append(
                        picture.width_hwpunit / picture.height_hwpunit
                    )
        aspect_preserved = bool(observed_ratios) and all(
            abs(ratio - expected.aspect_ratio) <= ratio_tolerance
            for ratio in observed_ratios
        )
        if not byte_fidelity or not aspect_preserved:
            complete = False
        evidence.append(
            G04EmbeddedAssetEvidence(
                source_ref=expected.source_ref,
                expected_sha256=expected.byte_sha256,
                embedded_sha256s=embedded_hashes,
                new_bin_items=new_ids,
                byte_fidelity=byte_fidelity,
                expected_aspect_ratio=expected.aspect_ratio,
                observed_aspect_ratios=tuple(observed_ratios),
                aspect_preserved=aspect_preserved,
            )
        )
    return G04EmbeddedProof(tuple(evidence), complete)


def png_dimensions(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, ValueError) as error:
        raise HwpLiveError(f"G04 렌더 PNG를 읽지 못했습니다: {path}") from error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise HwpLiveError(f"G04 산출물 SHA-256을 읽지 못했습니다: {path}") from error
    return digest.hexdigest()


__all__ = [
    "G04EmbeddedProof",
    "G04ImageExpectation",
    "G04LoweredPagePlan",
    "G04PreparationError",
    "HwpmlBinary",
    "HwpmlImageInspection",
    "HwpmlPicture",
    "g04_error_from_exception",
    "g04_overflow_hint",
    "inspect_hwpml_images",
    "CAPTION_FLOW_HEIGHT_MM",
    "TABLE_FLOW_LEADING_MM",
    "G04Placement",
    "PositionedPlanBlock",
    "lower_page_plan",
    "plan_block_order",
    "plan_vertical_leads",
    "png_dimensions",
    "table_row_heights_mm",
    "prepare_g04_application",
    "source_evidence_for_plan",
    "sha256_file",
    "validate_g03_result",
    "verify_embedded_images",
]
