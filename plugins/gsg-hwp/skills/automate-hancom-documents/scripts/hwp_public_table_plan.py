from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from hwp_live_structure_contract import DocumentStructure, StructureTable
from hwp_live_template_repeat import (
    TableTemplateRepeatPlan,
    TemplateImageCell,
    TemplateTableBlock,
    TemplateTextCell,
)
from hwp_live_workflow_table import resolve_workflow_table, workflow_page
from hwp_operation_contract import (
    HwpOperateTarget,
    OperationResult,
)
from hwp_operation_registry import operation_registry
from hwp_public_contract import PublicTableTarget, SeriesItem
from hwp_public_table_mapping import (
    TablePlanInputFailure,
    map_table_cells,
    prefix_failure,
    select_table_cell,
)
from hwp_public_table_target import (
    PublicTableTargetStore,
    UnknownPublicTargetError,
    unknown_public_target_result,
)


_TEMPLATE_IMAGE_WIDTH_MM = 80.0
_TEMPLATE_IMAGE_HEIGHT_MM = 40.0


class PublicTableStructureReader(Protocol):
    async def read_table_structure(
        self,
        document_path: str | None,
        page: int,
    ) -> DocumentStructure: ...


@dataclass(frozen=True, slots=True)
class ResolvedPublicTable:
    document_path: str | None
    structure: DocumentStructure
    table: StructureTable
    table_index: int
    target: HwpOperateTarget


@dataclass(frozen=True, slots=True)
class PublicTableResolutionRequest:
    target: PublicTableTarget | None
    request_id: str
    query: str


async def resolve_public_table(
    reader: PublicTableStructureReader,
    targets: PublicTableTargetStore,
    request: PublicTableResolutionRequest,
) -> ResolvedPublicTable | OperationResult:
    try:
        requested = targets.resolve(request.target)
    except UnknownPublicTargetError as error:
        return unknown_public_target_result(
            error,
            request_id=request.request_id,
            query=request.query,
        )
    structure = await reader.read_table_structure(
        requested.document_path,
        workflow_page(requested.target),
    )
    resolved = resolve_workflow_table(structure, requested.target)
    if resolved.table is None:
        candidates = resolved.candidates
        return OperationResult(
            request_id=request.request_id,
            status="ambiguous" if candidates else "not_found",
            query=request.query,
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            target_candidates=candidates,
            message=(
                "대상 표가 여러 개입니다"
                if candidates
                else "대상 조건과 일치하는 표가 없습니다"
            ),
        )
    control_id = resolved.table.control_instance_id
    if control_id is None:
        return OperationResult(
            request_id=request.request_id,
            status="known_failure",
            query=request.query,
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            failure_stage="target_resolve",
            message="대상 표의 네이티브 식별자를 읽지 못했습니다",
            retry_safe=False,
        )
    assert resolved.table_index is not None
    return ResolvedPublicTable(
        document_path=requested.document_path,
        structure=structure,
        table=resolved.table,
        table_index=resolved.table_index,
        target=HwpOperateTarget(
            kind="table",
            binding="active",
            page_hint=resolved.table.page_start,
            table_index=resolved.table_index,
            match_policy="return_candidates",
            control_instance_id=control_id,
        ),
    )


def repeat_plan(
    resolved: ResolvedPublicTable,
    count: int,
    caption_pattern: str | None,
) -> TableTemplateRepeatPlan:
    assert resolved.table.control_instance_id is not None
    return TableTemplateRepeatPlan(
        source_page=resolved.table.page_start,
        source_control_id=resolved.table.control_instance_id,
        caption_title=caption_pattern,
        blocks=tuple(TemplateTableBlock() for _ in range(count)),
    )


def series_plan(
    resolved: ResolvedPublicTable,
    items: tuple[SeriesItem, ...],
) -> TableTemplateRepeatPlan | TablePlanInputFailure:
    blocks: list[TemplateTableBlock] = []
    captions = tuple(dict.fromkeys(item.caption for item in items if item.caption is not None))
    if len(captions) > 1:
        return TablePlanInputFailure("items.caption", "서로 다른 캡션은 한 번에 적용할 수 없습니다")
    for index, item in enumerate(items):
        mapped_result = map_table_cells(
            resolved.table,
            tuple((*item.values, *item.images)),
        )
        if isinstance(mapped_result, TablePlanInputFailure):
            field_group = "values" if mapped_result.field in item.values else "images"
            return prefix_failure(
                mapped_result,
                f"items[{index}].{field_group}",
            )
        mapped = mapped_result.by_key
        values = [
            TemplateTextCell(
                address=mapped[key].address,
                expected_text=mapped[key].text,
                replacement=value,
            )
            for key, value in item.values.items()
        ]
        images = [
            TemplateImageCell(
                address=mapped[key].address,
                expected_text=mapped[key].text,
                path=path,
                width_mm=_TEMPLATE_IMAGE_WIDTH_MM,
                height_mm=_TEMPLATE_IMAGE_HEIGHT_MM,
            )
            for key, path in item.images.items()
        ]
        for selector_index, requested in enumerate(item.text_cells):
            selected = select_table_cell(resolved.table, requested)
            if isinstance(selected, TablePlanInputFailure):
                return prefix_failure(
                    selected,
                    f"items[{index}].text_cells[{selector_index}]",
                )
            values.append(
                TemplateTextCell(
                    address=selected.address,
                    expected_text=selected.text,
                    replacement=requested.value,
                )
            )
        for selector_index, requested in enumerate(item.image_cells):
            selected = select_table_cell(resolved.table, requested)
            if isinstance(selected, TablePlanInputFailure):
                return prefix_failure(
                    selected,
                    f"items[{index}].image_cells[{selector_index}]",
                )
            images.append(
                TemplateImageCell(
                    address=selected.address,
                    expected_text=selected.text,
                    path=requested.path,
                    width_mm=_TEMPLATE_IMAGE_WIDTH_MM,
                    height_mm=_TEMPLATE_IMAGE_HEIGHT_MM,
                )
            )
        if {cell.address for cell in values} & {image.address for image in images}:
            return TablePlanInputFailure(
                f"items[{index}]",
                "같은 셀에 값과 그림을 동시에 지정할 수 없습니다",
            )
        text_addresses = tuple(cell.address for cell in values)
        image_addresses = tuple(image.address for image in images)
        if len(set(text_addresses)) != len(text_addresses):
            return TablePlanInputFailure(
                f"items[{index}].text_cells",
                "같은 셀에 값을 두 번 지정할 수 없습니다",
            )
        if len(set(image_addresses)) != len(image_addresses):
            return TablePlanInputFailure(
                f"items[{index}].image_cells",
                "같은 셀에 그림을 두 번 지정할 수 없습니다",
            )
        blocks.append(
            TemplateTableBlock(text_cells=tuple(values), images=tuple(images))
        )
    assert resolved.table.control_instance_id is not None
    return TableTemplateRepeatPlan(
        source_page=resolved.table.page_start,
        source_control_id=resolved.table.control_instance_id,
        caption_title=None if not captions else captions[0],
        blocks=tuple(blocks),
    )
