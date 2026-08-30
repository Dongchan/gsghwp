from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, field_validator, model_validator

from hwp_live_contract import PageSetup
from hwp_live_values import ContractModel
from hwp_pageplan_assets import SlotMap

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CandidateId = Literal["A", "B"]
G01_ROUNDING_TOLERANCE_MM = 0.01


def _logical_ref(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("logical reference must be non-empty")
    if any(char in value for char in ("\\", "<", ">", '"', "'", "\n", "\r")):
        raise ValueError("logical reference contains unsafe syntax")
    if value.startswith(("http:", "https:", "file:", "/")):
        raise ValueError("logical reference cannot be external or absolute")
    return value


LogicalRef = Annotated[str, BeforeValidator(_logical_ref)]
StyleRoleName = Annotated[str, BeforeValidator(_logical_ref)]


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_g01_body_geometry_sha256(
    page_setup: PageSetup,
    body_box: MmBox,
) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "body_box": body_box.model_dump(mode="json"),
                "page_setup": page_setup.model_dump(mode="json"),
            }
        )
    )


class MmBox(ContractModel):
    left_mm: float = Field(ge=0)
    top_mm: float = Field(ge=0)
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)

    @property
    def right_mm(self) -> float:
        return self.left_mm + self.width_mm

    @property
    def bottom_mm(self) -> float:
        return self.top_mm + self.height_mm

    def contains(self, inner: MmBox, tolerance_mm: float = 1e-6) -> bool:
        return (
            inner.left_mm >= self.left_mm - tolerance_mm
            and inner.top_mm >= self.top_mm - tolerance_mm
            and inner.right_mm <= self.right_mm + tolerance_mm
            and inner.bottom_mm <= self.bottom_mm + tolerance_mm
        )


class G01Observation(ContractModel):
    document_revision_hash: Sha256
    page_setup_hash: Sha256
    convention_profile_hash: Sha256
    body_geometry_hash: Sha256
    page_setup: PageSetup
    body_box: MmBox

    @model_validator(mode="after")
    def validate_geometry(self) -> G01Observation:
        setup = self.page_setup
        if setup.paper_width_mm <= 0 or setup.paper_height_mm <= 0:
            raise ValueError("G01 paper dimensions must be positive")
        margins = (
            setup.top_margin_mm,
            setup.bottom_margin_mm,
            setup.left_margin_mm,
            setup.right_margin_mm,
            setup.header_mm,
            setup.footer_mm,
            setup.gutter_mm,
        )
        if any(value < 0 for value in margins):
            raise ValueError("G01 setup dimensions cannot be negative")
        paper = MmBox(
            left_mm=0,
            top_mm=0,
            width_mm=setup.paper_width_mm,
            height_mm=setup.paper_height_mm,
        )
        if not paper.contains(self.body_box):
            raise ValueError("G01 body box must be inside the observed paper")
        if (
            self.body_box.width_mm
            > setup.paper_width_mm
            - setup.left_margin_mm
            - setup.right_margin_mm
            + G01_ROUNDING_TOLERANCE_MM
        ):
            raise ValueError("G01 body box exceeds observed horizontal margins")
        if (
            self.body_box.height_mm
            > setup.paper_height_mm
            - setup.top_margin_mm
            - setup.bottom_margin_mm
            + G01_ROUNDING_TOLERANCE_MM
        ):
            raise ValueError("G01 body box exceeds observed vertical margins")
        if self.body_geometry_hash != canonical_g01_body_geometry_sha256(
            setup,
            self.body_box,
        ):
            raise ValueError("G01 body geometry hash mismatch")
        return self


class ObservedWidthLabel(ContractModel):
    page: int = Field(ge=1)
    slot_id: int = Field(ge=1)
    width_mm: float = Field(gt=0)
    body_width_mm: float = Field(gt=0)
    body_fraction: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_fraction(self) -> ObservedWidthLabel:
        if abs(self.width_mm / self.body_width_mm - self.body_fraction) > 1e-6:
            raise ValueError("observed width label fraction mismatch")
        return self


class ParagraphPlanBlock(ContractModel):
    kind: Literal["paragraph"]
    block_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    semantic_role: Literal["body", "heading", "caption", "table_title"]
    source_ref: LogicalRef
    content_sha256: Sha256
    style_role: StyleRoleName
    box: MmBox


class TablePlanBlock(ContractModel):
    kind: Literal["table"]
    block_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    semantic_role: Literal["table", "table_title"]
    source_ref: LogicalRef
    content_sha256: Sha256
    style_role: StyleRoleName
    box: MmBox


class ImagePlanBlock(ContractModel):
    kind: Literal["image"]
    block_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    semantic_role: Literal["figure"] = "figure"
    slot_id: int = Field(ge=1)
    fit: Literal["contain"] = "contain"
    box: MmBox
    style_role: StyleRoleName | None = None
    caption_source_ref: LogicalRef | None = None
    caption_content_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_caption(self) -> ImagePlanBlock:
        if (self.caption_source_ref is None) != (self.caption_content_sha256 is None):
            raise ValueError("caption source and hash must be supplied together")
        return self


class PageBreakPlanBlock(ContractModel):
    kind: Literal["page_break"]
    block_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


PagePlanBlock = Annotated[
    ParagraphPlanBlock | TablePlanBlock | ImagePlanBlock | PageBreakPlanBlock,
    Field(discriminator="kind"),
]


class PagePlanPage(ContractModel):
    page: int = Field(ge=1)
    page_setup: PageSetup
    body_box: MmBox
    blocks: tuple[PagePlanBlock, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_blocks(self) -> PagePlanPage:
        ids: set[str] = set()
        for block in self.blocks:
            if block.block_id in ids:
                raise ValueError(f"duplicate block_id: {block.block_id}")
            ids.add(block.block_id)
            if not isinstance(block, PageBreakPlanBlock) and not self.body_box.contains(
                block.box
            ):
                raise ValueError(f"block {block.block_id} is outside the body box")
        return self


class PagePlanCompilerMeta(ContractModel):
    canonical_plan_sha256: Sha256
    source_manifest_sha256: Sha256
    style_roles_sha256: Sha256
    ordered_source_refs: tuple[LogicalRef, ...]
    figure_slot_order: tuple[int, ...]
    figure_slot_count: int = Field(ge=0)
    page_count: int = Field(ge=1, le=2)
    block_count: int = Field(ge=1)
    g01_document_revision_hash: Sha256
    g01_page_setup_hash: Sha256
    g01_convention_profile_hash: Sha256
    g01_body_geometry_hash: Sha256


def _blocks(pages: Iterable[PagePlanPage]) -> tuple[PagePlanBlock, ...]:
    return tuple(block for page in pages for block in page.blocks)


def _ordered_source_refs(
    pages: Iterable[PagePlanPage],
    slot_map: SlotMap,
) -> tuple[str, ...]:
    ordered: list[str] = []

    def add(value: str | None) -> None:
        if value is not None and value not in ordered:
            ordered.append(value)

    for block in _blocks(pages):
        if isinstance(block, (ParagraphPlanBlock, TablePlanBlock)):
            add(block.source_ref)
        elif isinstance(block, ImagePlanBlock):
            add(block.caption_source_ref)
            slot = slot_map.require(block.slot_id)
            add(slot.source if slot.kind == "image" else slot.source_ref)
    for slot in slot_map.slots:
        add(slot.source if slot.kind == "image" else slot.source_ref)
    return tuple(ordered)


def _image_slots(slot_map: SlotMap) -> tuple[int, ...]:
    return tuple(slot.slot_id for slot in slot_map.slots if slot.kind == "image")


def _canonical_payload(page_plan: PagePlan) -> dict[str, object]:
    return page_plan.model_dump(
        mode="json",
        by_alias=True,
        exclude={"compiler_meta"},
    )


def canonical_page_plan_sha256(page_plan: PagePlan) -> str:
    return sha256_bytes(canonical_json_bytes(_canonical_payload(page_plan)))


class PagePlan(ContractModel):
    schema_id: Literal["gsg.hwp.page-plan.v1"] = Field(alias="schema")
    candidate: CandidateId
    pages: tuple[PagePlanPage, ...] = Field(min_length=1, max_length=2)
    preview_count: int = Field(ge=1, le=2)
    source_manifest_sha256: Sha256
    style_roles_sha256: Sha256
    slot_map: SlotMap
    g01_observation: G01Observation
    observed_width_labels: tuple[ObservedWidthLabel, ...] = ()
    compiler_meta: PagePlanCompilerMeta = Field(alias="_compiler_meta")

    @field_validator("observed_width_labels")
    @classmethod
    def unique_width_labels(
        cls,
        labels: tuple[ObservedWidthLabel, ...],
    ) -> tuple[ObservedWidthLabel, ...]:
        keys = [(label.page, label.slot_id) for label in labels]
        if len(set(keys)) != len(keys):
            raise ValueError("observed width labels must be unique")
        return labels

    @model_validator(mode="after")
    def validate_integrity(self) -> PagePlan:
        expected_pages = 1 if self.candidate == "A" else 2
        if len(self.pages) != expected_pages or self.preview_count != expected_pages:
            raise ValueError("candidate must have exact page and preview cardinality")
        if self.slot_map.candidate != self.candidate:
            raise ValueError("slot map candidate mismatch")
        if tuple(page.page for page in self.pages) != tuple(
            range(1, expected_pages + 1)
        ):
            raise ValueError("PagePlan pages must be contiguous and ordered")
        block_items = _blocks(self.pages)
        if len({block.block_id for block in block_items}) != len(block_items):
            raise ValueError("PagePlan block IDs must be globally unique")
        expected_body = self.g01_observation.body_box
        for page in self.pages:
            if page.page_setup != self.g01_observation.page_setup:
                raise ValueError("page setup differs from G01 observation")
            if not expected_body.contains(page.body_box) or not page.body_box.contains(
                expected_body
            ):
                raise ValueError("body box differs from G01 observation")
        image_blocks = tuple(
            block for block in block_items if isinstance(block, ImagePlanBlock)
        )
        expected_slots = _image_slots(self.slot_map)
        actual_slots = tuple(block.slot_id for block in image_blocks)
        if actual_slots != expected_slots:
            counts = {
                slot_id: actual_slots.count(slot_id)
                for slot_id in sorted(set(actual_slots))
                if actual_slots.count(slot_id) > 1
            }
            duplicate_blocks = [
                block.block_id
                for block in image_blocks
                if actual_slots.count(block.slot_id) > 1
            ]
            if counts:
                detail = (
                    f"duplicate image slot placement: slots={counts}, "
                    f"blocks={duplicate_blocks}"
                )
                raise ValueError(detail)
            raise ValueError("image blocks do not match ordered image slots")
        labels = {
            (label.page, label.slot_id): label for label in self.observed_width_labels
        }
        for page in self.pages:
            for block in page.blocks:
                if not isinstance(block, ImagePlanBlock):
                    continue
                label = labels.get((page.page, block.slot_id))
                if label is None:
                    raise ValueError(
                        "each image block requires an observed width label"
                    )
                if abs(label.width_mm - block.box.width_mm) > 1e-6:
                    raise ValueError("observed width does not match image box")
                if abs(label.body_width_mm - page.body_box.width_mm) > 1e-6:
                    raise ValueError("observed width does not match body width")
        meta = self.compiler_meta
        if meta.canonical_plan_sha256 != canonical_page_plan_sha256(self):
            raise ValueError("_compiler_meta plan hash mismatch")
        if meta.source_manifest_sha256 != self.source_manifest_sha256:
            raise ValueError("_compiler_meta source manifest hash mismatch")
        if meta.style_roles_sha256 != self.style_roles_sha256:
            raise ValueError("_compiler_meta style roles hash mismatch")
        if meta.ordered_source_refs != _ordered_source_refs(self.pages, self.slot_map):
            raise ValueError("_compiler_meta source ref order mismatch")
        if meta.figure_slot_order != expected_slots or meta.figure_slot_count != len(
            expected_slots
        ):
            raise ValueError("_compiler_meta figure slot order/count mismatch")
        if meta.page_count != len(self.pages) or meta.block_count != len(block_items):
            raise ValueError("_compiler_meta page/block totals mismatch")
        observation = self.g01_observation
        if (
            meta.g01_document_revision_hash != observation.document_revision_hash
            or meta.g01_page_setup_hash != observation.page_setup_hash
            or meta.g01_convention_profile_hash != observation.convention_profile_hash
            or meta.g01_body_geometry_hash != observation.body_geometry_hash
        ):
            raise ValueError("_compiler_meta G01 hash mismatch")
        return self


def build_page_plan(
    *,
    candidate: CandidateId,
    pages: tuple[PagePlanPage, ...],
    slot_map: SlotMap,
    source_manifest_sha256: str,
    style_roles_sha256: str,
    observation: G01Observation,
    observed_width_labels: tuple[ObservedWidthLabel | Mapping[str, object], ...],
) -> PagePlan:
    labels = tuple(
        label
        if isinstance(label, ObservedWidthLabel)
        else ObservedWidthLabel.model_validate(label)
        for label in observed_width_labels
    )
    image_slot_order = _image_slots(slot_map)
    placeholder = PagePlanCompilerMeta(
        canonical_plan_sha256="0" * 64,
        source_manifest_sha256=source_manifest_sha256,
        style_roles_sha256=style_roles_sha256,
        ordered_source_refs=_ordered_source_refs(pages, slot_map),
        figure_slot_order=image_slot_order,
        figure_slot_count=len(image_slot_order),
        page_count=len(pages),
        block_count=len(_blocks(pages)),
        g01_document_revision_hash=observation.document_revision_hash,
        g01_page_setup_hash=observation.page_setup_hash,
        g01_convention_profile_hash=observation.convention_profile_hash,
        g01_body_geometry_hash=observation.body_geometry_hash,
    )
    draft = PagePlan.model_construct(
        schema_id="gsg.hwp.page-plan.v1",
        candidate=candidate,
        pages=pages,
        preview_count=1 if candidate == "A" else 2,
        source_manifest_sha256=source_manifest_sha256,
        style_roles_sha256=style_roles_sha256,
        slot_map=slot_map,
        g01_observation=observation,
        observed_width_labels=labels,
        compiler_meta=placeholder,
    )
    meta = placeholder.model_copy(
        update={"canonical_plan_sha256": canonical_page_plan_sha256(draft)}
    )
    payload = draft.model_dump(mode="json", by_alias=True)
    payload["_compiler_meta"] = meta.model_dump(mode="json")
    return PagePlan.model_validate(payload)


def revalidate_page_plan(page_plan: object) -> PagePlan:
    if not isinstance(page_plan, PagePlan):
        raise TypeError("page input must be PagePlan")
    return PagePlan.model_validate(page_plan.model_dump(mode="json", by_alias=True))


def revalidate_source_registry(source_registry: object) -> object:
    from hwp_pageplan_assets import SourceRegistry

    if not isinstance(source_registry, SourceRegistry):
        raise TypeError("source input must be SourceRegistry")
    return SourceRegistry.model_validate(
        source_registry.model_dump(mode="json", by_alias=True)
    )


def verify_page_plan_sources(
    page_plan: object,
    source_registry: object,
    *,
    require_final_assets: bool,
) -> None:
    from hwp_pageplan_assets import SourceRegistry, verify_source_registry

    page_plan = revalidate_page_plan(page_plan)
    source_registry = revalidate_source_registry(source_registry)
    if not isinstance(source_registry, SourceRegistry):
        raise TypeError("source input must be SourceRegistry")
    verify_source_registry(
        source_registry,
        require_final_assets=require_final_assets,
    )
    if page_plan.source_manifest_sha256 != source_registry.manifest_sha256:
        raise ValueError("PagePlan source manifest hash mismatch")
    if (
        page_plan.g01_observation.convention_profile_hash
        != source_registry.convention_profile_hash
    ):
        raise ValueError("PagePlan convention profile hash mismatch")
    if page_plan.style_roles_sha256 != source_registry.style_roles_sha256:
        raise ValueError("PagePlan style roles provenance mismatch")
    for slot in page_plan.slot_map.slots:
        if slot.kind == "image":
            source = source_registry.require_image(slot.source or "")
            if source.source_ref != slot.source:
                raise ValueError(f"slot {slot.slot_id} source provenance mismatch")
            if source.source_kind != slot.source_kind:
                raise ValueError(f"slot {slot.slot_id} source_kind provenance mismatch")
            if source.quality != slot.quality:
                raise ValueError(f"slot {slot.slot_id} quality provenance mismatch")
            if source.final_insertable != slot.final_insertable:
                raise ValueError(
                    f"slot {slot.slot_id} final_insertable provenance mismatch"
                )
            if source.byte_sha256 != slot.asset_sha256:
                raise ValueError(f"slot {slot.slot_id} raw asset SHA-256 mismatch")
            if require_final_assets and not slot.final_insertable:
                raise ValueError(f"slot {slot.slot_id} is not final-insertable")
        elif slot.kind == "text":
            _ = source_registry.require_text(slot.source_ref or "")
        else:
            _ = source_registry.require_table(slot.source_ref or "")
    for page in page_plan.pages:
        for block in page.blocks:
            if isinstance(block, ParagraphPlanBlock):
                source = source_registry.require_text(block.source_ref)
                if source.content_sha256 != block.content_sha256:
                    raise ValueError(
                        f"paragraph source hash mismatch: {block.source_ref}"
                    )
                _ = source_registry.style(block.style_role)
            elif isinstance(block, TablePlanBlock):
                source = source_registry.require_table(block.source_ref)
                if source.content_sha256 != block.content_sha256:
                    raise ValueError(f"table source hash mismatch: {block.source_ref}")
                _ = source_registry.style(block.style_role)
            elif isinstance(block, ImagePlanBlock):
                slot = page_plan.slot_map.require(block.slot_id)
                if slot.kind != "image":
                    raise ValueError(
                        f"image block slot {block.slot_id} is not an image"
                    )
                source = source_registry.require_image(slot.source or "")
                if source.byte_sha256 != slot.asset_sha256:
                    raise ValueError(f"image block source hash mismatch: {slot.source}")
                if block.caption_source_ref is not None:
                    caption = source_registry.require_text(block.caption_source_ref)
                    if caption.content_sha256 != block.caption_content_sha256:
                        raise ValueError(
                            f"caption source hash mismatch: {block.caption_source_ref}"
                        )


class CompilerBoundaryReceipt(ContractModel):
    analysis_skipped: Literal[True] = True
    image_analyzer_called: Literal[False] = False
    source_refs_verified: Literal[True] = True
    body_boxes_verified: Literal[True] = True


def validate_page_plan_for_compiler(
    page_plan: object,
    source_registry: object,
) -> CompilerBoundaryReceipt:
    """Validate structured input only; HTML/SVG/DOM/CSS never enters this seam."""
    page_plan = revalidate_page_plan(page_plan)
    source_registry = revalidate_source_registry(source_registry)
    verify_page_plan_sources(
        page_plan,
        source_registry,
        require_final_assets=True,
    )
    return CompilerBoundaryReceipt(
        analysis_skipped=True,
        image_analyzer_called=False,
        source_refs_verified=True,
        body_boxes_verified=True,
    )


# G02 CONTRACT END
