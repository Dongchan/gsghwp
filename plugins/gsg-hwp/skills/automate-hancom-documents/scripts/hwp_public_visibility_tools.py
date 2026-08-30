from __future__ import annotations

from pathlib import Path
from typing import Annotated, Protocol, final
from pydantic import Field

from hwp_operation_contract import (
    HwpOperateGuards,
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    OperationResult,
)
from hwp_operation_registry import operation_registry
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs
from hwp_public_action_contract import PublicOperationId
from hwp_public_contract import (
    PublicActionResult,
    to_public_action_result,
)
from hwp_public_table_plan import PublicTableStructureReader, ResolvedPublicTable
from hwp_public_visibility_resolver import (
    VisibilityTableRole,
    resolve_visibility_table,
)
from hwp_visibility_series_contract import VisibilitySeriesPlanError
from hwp_visibility_template import build_visibility_series_plan_with_observation


SYNC_VISIBILITY_INTENT = "예비조망점표 기준 가시권 분석표 동기화"


class PublicVisibilityExecutor(PublicTableStructureReader, Protocol):
    async def execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationResult: ...


def _failure(request_id: str, message: str) -> OperationResult:
    return OperationResult(
        request_id=request_id,
        status="known_failure",
        query=SYNC_VISIBILITY_INTENT,
        registry_entries=operation_registry().count,
        lookup_microseconds=0,
        failure_stage="visibility_series_plan",
        message=message,
        retry_safe=True,
    )


@final
class HwpPublicVisibilityTools:
    __slots__ = ("_executor",)

    def __init__(
        self,
        executor: PublicVisibilityExecutor,
    ) -> None:
        self._executor = executor

    def _public(
        self,
        result: OperationResult,
    ) -> PublicActionResult:
        return to_public_action_result(result, ())

    async def _resolved(
        self,
        document_path: str | None,
        page: int,
        role: VisibilityTableRole,
    ) -> ResolvedPublicTable | PublicActionResult:
        resolved = await resolve_visibility_table(
            self._executor,
            document_path,
            page,
            role,
        )
        if isinstance(resolved, OperationResult):
            return self._public(resolved)
        return resolved

    async def hwp_sync_visibility_analysis_tables(
        self,
        *,
        operation_id: PublicOperationId,
        source_page: Annotated[
            int,
            Field(ge=1, description="예비조망점 선정표가 있는 쪽"),
        ],
        template_page: Annotated[
            int,
            Field(ge=1, description="가시권 분석표 양식이 시작되는 쪽"),
        ],
        visibility_folder: Annotated[
            Path,
            Field(description="번호별 가시권분석 사진 폴더"),
        ],
        current_folder: Annotated[
            Path,
            Field(description="번호별 현황사진 폴더"),
        ],
        document_path: Annotated[
            str | None,
            Field(min_length=1, max_length=32_767),
        ] = None,
    ) -> PublicActionResult:
        source = await self._resolved(
            document_path,
            source_page,
            "source",
        )
        if isinstance(source, PublicActionResult):
            return source.model_copy(update={"request_id": operation_id})
        template = await self._resolved(
            document_path,
            template_page,
            "template",
        )
        if isinstance(template, PublicActionResult):
            return template.model_copy(update={"request_id": operation_id})
        try:
            plan, observation = build_visibility_series_plan_with_observation(
                source.table,
                template.table,
                visibility_folder,
                current_folder,
            )
        except VisibilitySeriesPlanError as error:
            result = self._public(_failure(operation_id, str(error)))
            return result.model_copy(
                update={
                    "visibility_template_observation": (error.template_observation,)
                    if error.template_observation is not None
                    else (),
                }
            )
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=template.document_path,
            operation="table.build_series",
            target=template.target,
            policy=HwpOperatePolicy(
                preserve_character_style=True,
                preserve_existing_images=False,
                ambiguity="return_candidates",
                atomic=False,
            ),
            postconditions=HwpOperatePostconditions(
                record_count=len(plan.blocks),
                verify_structure=True,
            ),
            recipe=HwpPriorityRecipeInputs(
                table_template=plan,
                reconcile_existing=True,
            ),
        )
        result = await self._executor.execute(
            SYNC_VISIBILITY_INTENT,
            inputs,
            None,
        )
        viewpoint_count = sum(
            1
            for block in plan.blocks
            for cell in block.text_cells
            if cell.replacement.startswith("예비조망점")
        )
        expected_images = viewpoint_count * 2
        placed_images = sum(len(block.images) for block in plan.blocks)
        guidance: tuple[str, ...] = ()
        if placed_images == 0:
            guidance = (
                "사진 폴더가 없거나 번호 사진을 찾지 못해 표 개수와 값만 동기화했습니다.",
            )
        elif placed_images < expected_images:
            guidance = (
                (
                    f"사진 {placed_images}/{expected_images}장만 매칭해 넣었습니다. "
                    + "없는 번호는 표 값만 채웠습니다."
                ),
            )
        public = self._public(result)
        return public.model_copy(
            update={
                "visibility_template_observation": (observation,),
                "input_guidance": (*public.input_guidance, *guidance),
            }
        )
