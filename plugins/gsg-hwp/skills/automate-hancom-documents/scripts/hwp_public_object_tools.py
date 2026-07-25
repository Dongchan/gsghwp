from __future__ import annotations

from pathlib import Path
from typing import final

import hwp_public_action_metadata as metadata
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

    @staticmethod
    def _unknown_target(request_id: str) -> PublicActionResult:
        return PublicActionResult(
            status="needs_target",
            message="앞선 후보 응답에서 반환된 target_id가 필요합니다",
            request_id=request_id,
            runtime=RUNTIME_BUILD_INFO,
            verified=False,
            modified=False,
            required_inputs=("target_id",),
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
        document_path: str | None = None,
    ) -> PublicActionResult:
        request_id = operation_id
        resolved = self._targets.resolve_picture(target_id, document_path)
        if resolved is None:
            return self._unknown_target(request_id)
        return await self._execute(
            metadata.REPLACE_IMAGE_INTENT,
            HwpOperateInputs(
                request_id=request_id,
                document=resolved.document_path,
                operation="image.replace",
                target=resolved.target,
                assets=HwpOperateAssets(images={"image": path}),
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
            return self._unknown_target(request_id)
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
