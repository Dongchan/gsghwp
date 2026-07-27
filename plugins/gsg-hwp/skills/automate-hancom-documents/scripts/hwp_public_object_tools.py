from __future__ import annotations

from pathlib import Path
from typing import final

import hwp_public_action_metadata as metadata
from hwp_object_control_types import (
    CAPTIONABLE_CONTROL_TYPES,
    PICTURE_CONTROL_TYPES,
    is_control_type,
    is_table_control_type,
)
from hwp_operation_contract import (
    HwpOperateAssets,
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    OperationResult,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs
from hwp_public_action_contract import (
    OBJECT_INPUT_ALIASES,
    PublicActionExecutor,
    PublicImageHeight,
    PublicImageInsertTarget,
    PublicImageSize,
    PublicImageWidth,
    PublicObjectTargetStore,
    PublicOperationId,
    PublicStyleId,
)
from hwp_public_contract import (
    PublicActionResult,
    PublicTargetCandidate,
    refresh_public_target_ids,
    to_public_action_result,
)
from hwp_runtime_identity import RUNTIME_BUILD_INFO


@final
class HwpPublicObjectTools:
    __slots__ = ("_executor", "_targets")

    def __init__(
        self,
        executor: PublicActionExecutor,
        targets: PublicObjectTargetStore | None = None,
    ) -> None:
        self._executor = executor
        self._targets = PublicObjectTargetStore() if targets is None else targets

    def _public_result(
        self,
        result: OperationResult,
        document_path: str | None,
    ) -> PublicActionResult:
        target_ids = refresh_public_target_ids(self._targets, result, document_path)
        return to_public_action_result(result, target_ids, OBJECT_INPUT_ALIASES)

    async def _execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
    ) -> PublicActionResult:
        result = await self._executor.execute(intent, inputs, None)
        return self._public_result(result, inputs.document)

    async def _unknown_target(
        self,
        request_id: str,
        document_path: str | None,
        *,
        picture_only: bool,
    ) -> PublicActionResult:
        inspection = await self._executor.inspect_page_fast(document_path, 0, False)
        allowed_types = (
            PICTURE_CONTROL_TYPES if picture_only else CAPTIONABLE_CONTROL_TYPES
        )
        table_index = 0
        candidates: list[PublicTargetCandidate] = []
        for control in inspection.controls:
            # 표 번호 세기와 후보 선별이 같은 판정 규칙을 써야 한다. 한쪽만
            # 대소문자를 무시하면 후보로는 뽑히면서 table_index 는 비는 개체가
            # 생긴다.
            is_table = is_table_control_type(control.control_type)
            if is_table:
                table_index += 1
            if (
                not is_control_type(control.control_type, allowed_types)
                or not control.instance_id
            ):
                continue
            candidates.append(
                PublicTargetCandidate(
                    target_id=control.instance_id,
                    page=inspection.page,
                    table_index=table_index if is_table else None,
                    rows=control.rows,
                    columns=control.columns,
                )
            )
            if len(candidates) == 3:
                break
        return PublicActionResult(
            status="needs_target",
            message=(
                "현재 쪽에서 다시 조회한 실제 대상 후보를 반환했습니다"
                if candidates
                else (
                    "현재 쪽에서 대상 후보를 찾지 못했습니다. hwp_inspect_page_fast로 "
                    "대상 쪽을 다시 조회한 뒤 instance_id를 target_id로 전달하세요"
                )
            ),
            request_id=request_id,
            runtime=RUNTIME_BUILD_INFO,
            verified=False,
            modified=False,
            required_inputs=("target_id",),
            target_candidates=tuple(candidates),
            retry_safe=True,
        )

    async def hwp_insert_image(
        self,
        *,
        operation_id: PublicOperationId,
        path: Path,
        width_mm: PublicImageWidth | None = None,
        height_mm: PublicImageHeight | None = None,
        document_path: str | None = None,
        target: PublicImageInsertTarget = "selection",
    ) -> PublicActionResult:
        size = PublicImageSize(width_mm=width_mm, height_mm=height_mm)
        canonical_target = {
            "selection": HwpOperateTarget(kind="selection", binding="selection"),
            "document_end": HwpOperateTarget(
                kind="document",
                binding="active",
                scope="document",
            ),
        }[target]
        return await self._execute(
            metadata.INSERT_IMAGE_INTENT,
            HwpOperateInputs(
                request_id=operation_id,
                document=document_path,
                operation="image.insert",
                target=canonical_target,
                assets=HwpOperateAssets(images={"image": path}),
                policy=HwpOperatePolicy(ambiguity="return_candidates"),
                postconditions=HwpOperatePostconditions(verify_structure=True),
                recipe=HwpPriorityRecipeInputs(
                    picture_width_mm=size.width_mm,
                    picture_height_mm=size.height_mm,
                ),
            ),
        )

    async def hwp_replace_image(
        self,
        *,
        operation_id: PublicOperationId,
        path: Path,
        target_id: str | None = None,
        width_mm: PublicImageWidth | None = None,
        height_mm: PublicImageHeight | None = None,
        document_path: str | None = None,
    ) -> PublicActionResult:
        request_id = operation_id
        size = PublicImageSize(width_mm=width_mm, height_mm=height_mm)
        resolved = self._targets.resolve_picture(target_id, document_path)
        if resolved is None:
            return await self._unknown_target(
                request_id,
                document_path,
                picture_only=True,
            )
        return await self._execute(
            metadata.REPLACE_IMAGE_INTENT,
            HwpOperateInputs(
                request_id=request_id,
                document=resolved.document_path,
                operation="image.replace",
                target=resolved.target,
                assets=HwpOperateAssets(images={"image": path}),
                recipe=HwpPriorityRecipeInputs(
                    picture_width_mm=size.width_mm,
                    picture_height_mm=size.height_mm,
                ),
                policy=HwpOperatePolicy(ambiguity="return_candidates"),
                postconditions=HwpOperatePostconditions(verify_structure=True),
            ),
        )

    async def hwp_add_caption(
        self,
        *,
        operation_id: PublicOperationId,
        text: str,
        target_id: str | None = None,
        style_id: PublicStyleId = 0,
        document_path: str | None = None,
    ) -> PublicActionResult:
        request_id = operation_id
        resolved = self._targets.resolve_control(target_id, document_path)
        if resolved is None:
            return await self._unknown_target(
                request_id,
                document_path,
                picture_only=False,
            )
        return await self._execute(
            metadata.ADD_CAPTION_INTENT,
            HwpOperateInputs(
                request_id=request_id,
                document=resolved.document_path,
                operation="caption.add",
                target=resolved.target,
                recipe=HwpPriorityRecipeInputs(
                    caption_text=text,
                    caption_style_id=style_id,
                ),
                policy=HwpOperatePolicy(ambiguity="return_candidates"),
                postconditions=HwpOperatePostconditions(verify_structure=True),
            ),
        )
