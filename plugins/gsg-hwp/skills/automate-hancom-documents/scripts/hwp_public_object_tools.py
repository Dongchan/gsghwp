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
    OperationInputValue,
    OperationResult,
)
from hwp_priority_picture_edit import (
    COPY_DESTINATION_CELL_PARAMETER,
    COPY_DESTINATION_TABLE_PARAMETER,
    DESTINATION_CELL_PARAMETER,
    DESTINATION_TABLE_PARAMETER,
    SOURCE_CELL_PARAMETER,
    SOURCE_TABLE_PARAMETER,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs
from hwp_public_cell_selector import PublicCellAddress
from hwp_public_picture_edit_contract import (
    PublicPictureCrop,
    PublicPictureEditResult,
    PublicPicturePage,
    PublicPictureTableId,
    PublicPictureTargetId,
    to_public_picture_edit_result,
)
from hwp_public_action_contract import (
    OBJECT_INPUT_ALIASES,
    PublicActionExecutor,
    PublicImageFit,
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
        requested_target_id: str | None = None,
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
        target_notice = (
            ""
            if requested_target_id is None
            else f"지정한 target_id '{requested_target_id}'를 저장된 조회 대상과 연결하지 못했습니다. "
        )
        message = target_notice + (
            f"현재 커서 쪽인 {inspection.page}쪽에서 다시 조회한 실제 대상 "
            + "후보를 반환했습니다"
            if candidates
            else f"현재 커서 쪽인 {inspection.page}쪽에서 대상 후보를 찾지 못했습니다"
        )
        return PublicActionResult(
            status="needs_target",
            message=message,
            request_id=request_id,
            runtime=RUNTIME_BUILD_INFO,
            verified=False,
            modified=False,
            required_inputs=("target_id",),
            target_candidates=tuple(candidates),
            input_guidance=(
                f"target_candidates는 현재 커서 쪽인 {inspection.page}쪽 후보입니다. "
                + "대상이 다른 쪽이면 hwp_inspect_page_fast(page=<대상 쪽>, "
                + "document_selector=<같은 selector>)로 조회한 instance_id를 "
                + "target_id로 전달하세요.",
            ),
            retry_safe=True,
        )

    async def hwp_insert_image(
        self,
        *,
        operation_id: PublicOperationId,
        path: Path,
        width_mm: PublicImageWidth | None = None,
        height_mm: PublicImageHeight | None = None,
        fit: PublicImageFit = "contain",
        document_path: str | None = None,
        target: PublicImageInsertTarget = "selection",
    ) -> PublicActionResult:
        size = PublicImageSize(width_mm=width_mm, height_mm=height_mm, fit=fit)
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
                    picture_fit=size.fit,
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
                requested_target_id=target_id,
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

    async def hwp_edit_picture(
        self,
        *,
        operation_id: PublicOperationId,
        target_id: PublicPictureTargetId | None = None,
        table_id: PublicPictureTableId | None = None,
        cell: PublicCellAddress | None = None,
        move_to_cell: PublicCellAddress | None = None,
        move_to_table_id: PublicPictureTableId | None = None,
        crop: PublicPictureCrop | None = None,
        page: PublicPicturePage | None = None,
        document_path: str | None = None,
    ) -> PublicPictureEditResult:
        request_id = operation_id
        parameters: dict[str, OperationInputValue] = {}
        if table_id is not None:
            parameters[SOURCE_TABLE_PARAMETER] = table_id
        if cell is not None:
            parameters[SOURCE_CELL_PARAMETER] = cell
        if move_to_cell is not None:
            parameters[DESTINATION_CELL_PARAMETER] = move_to_cell
        if move_to_table_id is not None:
            parameters[DESTINATION_TABLE_PARAMETER] = move_to_table_id
        if crop is not None:
            parameters["crop_left"] = crop.left
            parameters["crop_top"] = crop.top
            parameters["crop_right"] = crop.right
            parameters["crop_bottom"] = crop.bottom
        resolved = self._targets.resolve_picture(target_id, document_path)
        if resolved is None:
            base = await self._unknown_target(
                request_id,
                document_path,
                picture_only=True,
                requested_target_id=target_id,
            )
            return to_public_picture_edit_result(base, None)
        target = resolved.target
        # 셀 주소로 지목한 요청은 개체 ID 없이 온다. 그때는 선택 상태를 기다리지
        # 말고 recipe 가 표·칸으로 찾게 둔다.
        if target_id is None and table_id is not None and cell is not None:
            target = target.model_copy(
                update={"binding": "active", "match_policy": "unique"}
            )
        if page is not None:
            target = target.model_copy(update={"page_hint": page})
        result = await self._executor.execute(
            metadata.EDIT_PICTURE_INTENT,
            HwpOperateInputs(
                request_id=request_id,
                document=resolved.document_path,
                operation="image.resize",
                target=target,
                parameters=parameters,
                policy=HwpOperatePolicy(ambiguity="return_candidates"),
                postconditions=HwpOperatePostconditions(verify_structure=True),
            ),
            None,
        )
        base = self._public_result(result, resolved.document_path)
        return to_public_picture_edit_result(base, result.picture_edit)

    async def hwp_copy_picture(
        self,
        *,
        operation_id: PublicOperationId,
        to_cell: PublicCellAddress,
        target_id: PublicPictureTargetId | None = None,
        table_id: PublicPictureTableId | None = None,
        cell: PublicCellAddress | None = None,
        to_table_id: PublicPictureTableId | None = None,
        width_mm: PublicImageWidth | None = None,
        height_mm: PublicImageHeight | None = None,
        page: PublicPicturePage | None = None,
        document_path: str | None = None,
    ) -> PublicPictureEditResult:
        request_id = operation_id
        size = PublicImageSize(width_mm=width_mm, height_mm=height_mm)
        parameters: dict[str, OperationInputValue] = {
            COPY_DESTINATION_CELL_PARAMETER: to_cell
        }
        if table_id is not None:
            parameters[SOURCE_TABLE_PARAMETER] = table_id
        if cell is not None:
            parameters[SOURCE_CELL_PARAMETER] = cell
        if to_table_id is not None:
            parameters[COPY_DESTINATION_TABLE_PARAMETER] = to_table_id
        resolved = self._targets.resolve_picture(target_id, document_path)
        if resolved is None:
            base = await self._unknown_target(
                request_id,
                document_path,
                picture_only=True,
                requested_target_id=target_id,
            )
            return to_public_picture_edit_result(base, None)
        target = resolved.target
        if target_id is None and table_id is not None and cell is not None:
            target = target.model_copy(
                update={"binding": "active", "match_policy": "unique"}
            )
        if page is not None:
            target = target.model_copy(update={"page_hint": page})
        result = await self._executor.execute(
            metadata.COPY_PICTURE_INTENT,
            HwpOperateInputs(
                request_id=request_id,
                document=resolved.document_path,
                operation="image.resize",
                target=target,
                parameters=parameters,
                recipe=HwpPriorityRecipeInputs(
                    picture_width_mm=size.width_mm,
                    picture_height_mm=size.height_mm,
                ),
                policy=HwpOperatePolicy(ambiguity="return_candidates"),
                postconditions=HwpOperatePostconditions(verify_structure=True),
            ),
            None,
        )
        base = self._public_result(result, resolved.document_path)
        return to_public_picture_edit_result(base, result.picture_edit)

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
                requested_target_id=target_id,
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
