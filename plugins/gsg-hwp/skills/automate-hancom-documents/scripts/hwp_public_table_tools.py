from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — the four table actions share one target-resolution and mapping pipeline.

from pathlib import Path
from typing import Annotated, Protocol, assert_never, final
from pydantic import Field
from typing_extensions import TypeIs

import hwp_public_table_metadata as metadata
from hwp_live_template_repeat import TableTemplateRepeatPlan, TemplateTableBlock
from hwp_operation_contract import (
    HwpOperateAssets,
    HwpOperateGuards,
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    OperationResult,
)
from hwp_operation_registry import operation_registry
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs
from hwp_priority_table_expand import data_record_count
from hwp_public_action_contract import PublicOperationId
from hwp_public_contract import (
    PublicActionResult,
    PublicTableTarget,
    SeriesItem,
    refresh_public_target_ids,
    to_public_action_result,
)
from hwp_public_table_mapping import MappedTableImages, RequestedImage
from hwp_public_table_mapping import TablePlanInputFailure, map_table_images
from hwp_public_table_plan import (
    PublicTableResolutionRequest,
    PublicTableStructureReader,
    ResolvedPublicTable,
    repeat_plan,
    resolve_public_table,
    series_plan,
)
from hwp_public_table_target import (
    CanonicalPublicTableTarget,
    PublicTableDataInput,
    PublicTableTargetStore,
    UnknownPublicTargetError,
)


class PublicTableExecutor(PublicTableStructureReader, Protocol):
    async def execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationResult: ...


def _new_request(
    intent: str,
    target: PublicTableTarget | None,
    operation_id: PublicOperationId,
) -> PublicTableResolutionRequest:
    return PublicTableResolutionRequest(
        target=target,
        request_id=operation_id,
        query=intent,
    )


def _needs_input_result(
    request: PublicTableResolutionRequest,
    failure: TablePlanInputFailure,
) -> OperationResult:
    return OperationResult(
        request_id=request.request_id,
        status="needs_input",
        query=request.query,
        registry_entries=operation_registry().count,
        lookup_microseconds=0,
        required_inputs=(failure.field,),
        message=failure.message,
        retry_safe=True,
    )


def _is_resolved_table(
    value: ResolvedPublicTable | OperationResult | PublicActionResult,
) -> TypeIs[ResolvedPublicTable]:
    return isinstance(value, ResolvedPublicTable)


def _is_repeat_plan(
    value: TableTemplateRepeatPlan | TablePlanInputFailure,
) -> TypeIs[TableTemplateRepeatPlan]:
    return isinstance(value, TableTemplateRepeatPlan)


def _is_mapped_images(
    value: MappedTableImages | TablePlanInputFailure,
) -> TypeIs[MappedTableImages]:
    return isinstance(value, MappedTableImages)


def _fast_inspection_repeat_plan(
    targets: PublicTableTargetStore,
    target: PublicTableTarget | None,
    count: int,
    caption_pattern: str | None,
) -> tuple[CanonicalPublicTableTarget, TableTemplateRepeatPlan] | None:
    try:
        selected = targets.resolve(target)
    except UnknownPublicTargetError:
        return None
    control_id = selected.target.control_instance_id
    page = selected.target.page_hint
    if control_id is None or page is None:
        return None
    return (
        selected,
        TableTemplateRepeatPlan(
            source_page=page,
            source_control_id=control_id,
            caption_title=caption_pattern,
            blocks=tuple(TemplateTableBlock() for _ in range(count)),
        ),
    )


@final
class HwpPublicTableTools:
    __slots__ = ("_executor", "_targets")

    def __init__(
        self,
        executor: PublicTableExecutor,
        targets: PublicTableTargetStore | None = None,
    ) -> None:
        self._executor = executor
        self._targets = PublicTableTargetStore() if targets is None else targets

    def _public_result(
        self,
        result: OperationResult,
        document_path: str | None,
    ) -> PublicActionResult:
        target_ids = refresh_public_target_ids(self._targets, result, document_path)
        return to_public_action_result(result, target_ids)

    async def _resolved(
        self,
        request: PublicTableResolutionRequest,
    ) -> ResolvedPublicTable | PublicActionResult:
        resolved = await resolve_public_table(self._executor, self._targets, request)
        match resolved:
            case OperationResult() as failed:
                document_path = (
                    None if request.target is None else request.target.document_path
                )
                return self._public_result(failed, document_path)
            case _ as unreachable if not _is_resolved_table(unreachable):
                assert_never(unreachable)
            case _:
                return resolved

    async def hwp_expand_and_fill_table(
        self,
        operation_id: PublicOperationId,
        target: PublicTableTarget | None = None,
        records: list[dict[str, str]] | None = None,
        cells: dict[str, str] | None = None,
        rows: list[list[str]] | None = None,
        start_cell: str | None = None,
        fill_blanks_only: bool = False,
        preserve_character_style: Annotated[
            bool,
            Field(
                description=(
                    "기존 셀의 글자 서식(글꼴·크기·색)을 유지합니다. 확장·채우기는 "
                    "표시 문자열을 재조립하지 않으므로 준 문자열이 그대로 들어갑니다."
                )
            ),
        ] = True,
    ) -> PublicActionResult:
        requested = PublicTableDataInput(
            target=target,
            records=None if records is None else tuple(records),
            cells=cells,
            rows=None if rows is None else tuple(tuple(row) for row in rows),
            start_cell=start_cell,
            fill_blanks_only=fill_blanks_only,
        )
        selected = self._targets.resolve(target)
        data = requested.to_canonical_data()
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=selected.document_path,
            operation="table.expand_and_fill",
            target=selected.target,
            data=data,
            policy=HwpOperatePolicy(
                preserve_character_style=preserve_character_style,
                fill_blanks_only=requested.fill_blanks_only,
                allow_row_expansion=True,
                ambiguity="return_candidates",
            ),
            postconditions=HwpOperatePostconditions(
                record_count=data_record_count(data),
                verify_structure=True,
            ),
        )
        result = await self._executor.execute(metadata.EXPAND_INTENT, inputs, None)
        return self._public_result(result, selected.document_path)

    async def hwp_repeat_table_template(
        self,
        *,
        operation_id: PublicOperationId,
        target: PublicTableTarget | None = None,
        count: Annotated[int, Field(ge=1, le=100)],
        caption_pattern: Annotated[
            str | None, Field(min_length=1, max_length=2_000)
        ] = None,
    ) -> PublicActionResult:
        request = _new_request(metadata.REPEAT_INTENT, target, operation_id)
        direct = _fast_inspection_repeat_plan(
            self._targets,
            target,
            count,
            caption_pattern,
        )
        if direct is not None:
            selected, plan = direct
            document_path = selected.document_path
            operation_target = selected.target
        else:
            resolved = await self._resolved(request)
            match resolved:
                case PublicActionResult():
                    return resolved
                case _ as unreachable if not _is_resolved_table(unreachable):
                    assert_never(unreachable)
                case _:
                    plan = repeat_plan(resolved, count, caption_pattern)
            document_path = resolved.document_path
            operation_target = resolved.target
        inputs = HwpOperateInputs(
            request_id=request.request_id,
            document=document_path,
            operation="table.repeat_template",
            target=operation_target,
            policy=HwpOperatePolicy(
                preserve_character_style=True,
                ambiguity="return_candidates",
            ),
            postconditions=HwpOperatePostconditions(
                record_count=count,
                verify_structure=True,
            ),
            recipe=HwpPriorityRecipeInputs(table_template=plan),
        )
        result = await self._executor.execute(metadata.REPEAT_INTENT, inputs, None)
        return self._public_result(result, document_path)

    async def hwp_build_table_series(
        self,
        *,
        operation_id: PublicOperationId,
        target: PublicTableTarget | None = None,
        items: Annotated[list[SeriesItem], Field(min_length=1, max_length=100)],
    ) -> PublicActionResult:
        request = _new_request(metadata.SERIES_INTENT, target, operation_id)
        resolved = await self._resolved(request)
        match resolved:
            case PublicActionResult():
                return resolved
            case _ as unreachable if not _is_resolved_table(unreachable):
                assert_never(unreachable)
            case _:
                planned = series_plan(resolved, tuple(items))
        match planned:
            case TablePlanInputFailure() as failure:
                return self._public_result(
                    _needs_input_result(request, failure), resolved.document_path
                )
            case _ as unreachable if not _is_repeat_plan(unreachable):
                assert_never(unreachable)
            case _:
                plan = planned
        inputs = HwpOperateInputs(
            request_id=request.request_id,
            document=resolved.document_path,
            operation="table.build_series",
            target=resolved.target,
            policy=HwpOperatePolicy(
                preserve_character_style=True,
                ambiguity="return_candidates",
            ),
            postconditions=HwpOperatePostconditions(
                record_count=len(items),
                verify_structure=True,
            ),
            recipe=HwpPriorityRecipeInputs(table_template=plan),
        )
        result = await self._executor.execute(metadata.SERIES_INTENT, inputs, None)
        return self._public_result(result, resolved.document_path)

    async def hwp_fill_table_images(
        self,
        *,
        operation_id: PublicOperationId,
        target: PublicTableTarget | None = None,
        images: Annotated[dict[str, Path], Field(min_length=1, max_length=200)],
        preserve_existing_images: bool = True,
    ) -> PublicActionResult:
        request = _new_request(metadata.TABLE_IMAGES_INTENT, target, operation_id)
        resolved = await self._resolved(request)
        match resolved:
            case PublicActionResult():
                return resolved
            case _ as unreachable if not _is_resolved_table(unreachable):
                assert_never(unreachable)
            case _:
                mapped = map_table_images(
                    resolved.table,
                    tuple(RequestedImage(key, path) for key, path in images.items()),
                )
        match mapped:
            case TablePlanInputFailure() as failure:
                return self._public_result(
                    _needs_input_result(request, failure), resolved.document_path
                )
            case _ as unreachable if not _is_mapped_images(unreachable):
                assert_never(unreachable)
            case _:
                canonical_images = mapped.images
        inputs = HwpOperateInputs(
            request_id=request.request_id,
            document=resolved.document_path,
            operation="table.insert_images",
            target=resolved.target,
            assets=HwpOperateAssets(images=canonical_images),
            policy=HwpOperatePolicy(
                preserve_existing_images=preserve_existing_images,
                ambiguity="return_candidates",
            ),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(
            metadata.TABLE_IMAGES_INTENT, inputs, None
        )
        return self._public_result(result, resolved.document_path)
