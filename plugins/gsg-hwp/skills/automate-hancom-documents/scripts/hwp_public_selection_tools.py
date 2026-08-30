from __future__ import annotations

from typing import Final, final

import hwp_public_action_metadata as metadata
from hwp_errors import HwpLiveError
from hwp_operation_contract import (
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs, RecipePosition
from hwp_public_action_contract import (
    SELECTION_INPUT_ALIASES,
    PublicFontName,
    PublicFontSize,
    PublicExpectedText,
    PublicLineSpacing,
    PublicOperationId,
    PublicReplacementText,
    PublicSelectionExecutor,
    PublicStyleId,
    PublicTextAlignment,
    PublicTextColor,
    PublicTextFormattingInput,
    PublicTextPatchBatch,
    PublicTextPatchPosition,
    PublicTextPatchPostSelection,
    PublicTextPatchTarget,
)
from hwp_public_contract import PublicActionResult, to_public_action_result
from hwp_live_text_patch_contract import TextPatchPlanGuard, TextPatchRequest


_REPLACE_SELECTED_TEXT_INTENT: Final = "선택 텍스트 교체"


def _text_patch_parameters(
    target: PublicTextPatchTarget,
    *,
    replacement: str | None,
    expected_text: str | None,
    formatting: PublicTextFormattingInput | None,
    post_selection: PublicTextPatchPostSelection = "keep",
) -> dict[str, str | int | float | bool]:
    parameters: dict[str, str | int | float | bool] = {
        "target_kind": target.kind,
        "match_case": target.match_case,
    }
    if replacement is not None:
        parameters["replacement"] = replacement
    if expected_text is not None:
        parameters["expected_text"] = expected_text
    if target.occurrence is not None:
        parameters["occurrence"] = target.occurrence
    if target.start is not None:
        parameters.update(
            {
                "start_list": target.start.list_id,
                "start_paragraph": target.start.paragraph,
                "start_character": target.start.character,
            }
        )
    if target.end is not None:
        parameters.update(
            {
                "end_list": target.end.list_id,
                "end_paragraph": target.end.paragraph,
                "end_character": target.end.character,
            }
        )
    if target.table_instance_id is not None:
        parameters["table_instance_id"] = target.table_instance_id
    if target.cell is not None:
        parameters["cell"] = target.cell.upper()
    if formatting is not None:
        parameters.update(formatting.to_parameters())
    parameters["post_selection"] = post_selection
    return parameters


@final
class HwpPublicSelectionTools:
    __slots__ = ("_executor",)

    def __init__(self, executor: PublicSelectionExecutor) -> None:
        self._executor = executor

    async def hwp_patch_text(
        self,
        *,
        operation_id: PublicOperationId,
        replacement: PublicReplacementText,
        target: PublicTextPatchTarget | None = None,
        expected_text: PublicExpectedText | None = None,
        formatting: PublicTextFormattingInput | None = None,
        post_selection: PublicTextPatchPostSelection = "keep",
        document_path: str | None = None,
    ) -> PublicActionResult:
        resolved_target = PublicTextPatchTarget() if target is None else target
        parameters = _text_patch_parameters(
            resolved_target,
            replacement=replacement,
            expected_text=expected_text,
            formatting=formatting,
            post_selection=post_selection,
        )
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation=(
                "text.insert"
                if resolved_target.kind == "current" and expected_text is None
                else "text.replace"
            ),
            parameters=parameters,
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.patch_text(
            metadata.PATCH_TEXT_INTENT,
            inputs,
            TextPatchRequest(
                target=resolved_target.to_live(),
                expected_text=expected_text,
                replacement=replacement,
                formatting=None if formatting is None else formatting.to_live(),
                post_selection=post_selection,
            ),
        )
        return to_public_action_result(result, (), SELECTION_INPUT_ALIASES)

    async def hwp_patch_text_batch(
        self,
        *,
        operation_id: PublicOperationId,
        patches: PublicTextPatchBatch,
        formatting: PublicTextFormattingInput | None = None,
        document_path: str | None = None,
        expected_document_id: int | None = None,
        expected_document_full_name: str | None = None,
        expected_content_revision: str | None = None,
    ) -> PublicActionResult:
        guard_values = (
            expected_document_id,
            expected_document_full_name,
            expected_content_revision,
        )
        if any(value is not None for value in guard_values) and any(
            value is None for value in guard_values
        ):
            raise HwpLiveError(
                "text.patch batch plan guards must be supplied together",
                mutation_started=False,
                safe_to_repeat=True,
            )
        plan_guard = (
            None
            if expected_document_id is None
            or expected_document_full_name is None
            or expected_content_revision is None
            else TextPatchPlanGuard(
                expected_document_id,
                expected_document_full_name,
                expected_content_revision,
            )
        )
        parameters: dict[str, str | int | float | bool] = {}
        requests: list[TextPatchRequest] = []
        live_formatting = None if formatting is None else formatting.to_live()
        for index, patch in enumerate(patches):
            item_parameters = _text_patch_parameters(
                patch.target,
                replacement=patch.replacement,
                expected_text=patch.expected_text,
                formatting=None,
            )
            parameters.update(
                {
                    f"patches.{index}.{key}": value
                    for key, value in item_parameters.items()
                }
            )
            live_patch = patch.to_live()
            requests.append(
                TextPatchRequest(
                    target=live_patch.target,
                    expected_text=live_patch.expected_text,
                    replacement=live_patch.replacement,
                    formatting=live_formatting,
                )
            )
        if formatting is not None:
            parameters.update(formatting.to_parameters())
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="text.replace",
            parameters=parameters,
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.patch_text_batch(
            metadata.PATCH_TEXT_BATCH_INTENT,
            inputs,
            tuple(requests),
            plan_guard,
        )
        return to_public_action_result(result, (), SELECTION_INPUT_ALIASES)

    async def hwp_replace_selected_text(
        self,
        *,
        operation_id: PublicOperationId,
        replacement: PublicReplacementText,
        target: PublicTextPatchTarget | None = None,
        expected_text: PublicExpectedText | None = None,
        document_path: str | None = None,
    ) -> PublicActionResult:
        resolved_target = PublicTextPatchTarget() if target is None else target
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation=(
                "document.replace_selection"
                if resolved_target.kind == "current" and expected_text is None
                else "text.replace"
            ),
            target=(
                HwpOperateTarget(kind="selection", binding="selection")
                if resolved_target.kind == "current" and expected_text is None
                else None
            ),
            parameters=_text_patch_parameters(
                resolved_target,
                replacement=replacement,
                expected_text=expected_text,
                formatting=None,
            ),
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        if resolved_target.kind == "current" and expected_text is None:
            result = await self._executor.replace_selected_text(
                _REPLACE_SELECTED_TEXT_INTENT,
                inputs,
                replacement,
            )
        else:
            result = await self._executor.patch_text(
                _REPLACE_SELECTED_TEXT_INTENT,
                inputs,
                TextPatchRequest(
                    target=resolved_target.to_live(),
                    expected_text=expected_text,
                    replacement=replacement,
                ),
            )
        return to_public_action_result(result, (), SELECTION_INPUT_ALIASES)

    async def hwp_apply_style(
        self,
        *,
        operation_id: PublicOperationId,
        style_id: PublicStyleId,
        target_position: PublicTextPatchPosition | None = None,
        document_path: str | None = None,
    ) -> PublicActionResult:
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="style.apply",
            target=HwpOperateTarget(kind="selection", binding="selection"),
            recipe=HwpPriorityRecipeInputs(
                style_id=style_id,
                target_position=(
                    None
                    if target_position is None
                    else RecipePosition(
                        list_id=target_position.list_id,
                        paragraph=target_position.paragraph,
                        character=target_position.character,
                    )
                ),
            ),
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(metadata.APPLY_STYLE_INTENT, inputs, None)
        return to_public_action_result(result, (), SELECTION_INPUT_ALIASES)

    async def hwp_format_text(
        self,
        *,
        operation_id: PublicOperationId,
        bold: bool | None = None,
        font_name: PublicFontName | None = None,
        font_size_pt: PublicFontSize | None = None,
        text_color: PublicTextColor | None = None,
        alignment: PublicTextAlignment = "inherit",
        line_spacing: PublicLineSpacing | None = None,
        target: PublicTextPatchTarget | None = None,
        expected_text: PublicExpectedText | None = None,
        document_path: str | None = None,
    ) -> PublicActionResult:
        resolved_target = PublicTextPatchTarget() if target is None else target
        formatting = PublicTextFormattingInput(
            bold=bold,
            font_name=font_name,
            font_size_pt=font_size_pt,
            text_color=text_color,
            alignment=alignment,
            line_spacing=line_spacing,
        )
        # 이 두 갈래는 parameters 를 읽는 쪽이 서로 다르다. 선택 갈래는 일반
        # 작업 파이프라인으로 가고 그쪽 parse_text_format 은 parameters 를 "적용할
        # 서식" 으로만 읽는다 -- target_kind·match_case 를 같이 실으면 서식 입력이
        # 아니라며 전부 거부했다(실측: alignment 하나만 줘도 schema_conflict).
        # 대상 지정은 inputs.target 이 이미 말하고 있으므로 여기서는 서식 값만
        # 싣는다. 지목 갈래는 네이티브 텍스트 패치로 가고 거기서 parameters 는
        # 무엇을 요청했는지의 기록이라 대상 지정까지 그대로 남긴다.
        selection_only = resolved_target.kind == "current" and expected_text is None
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="text.format",
            target=(
                HwpOperateTarget(kind="selection", binding="selection")
                if selection_only
                else None
            ),
            parameters=(
                dict(formatting.to_parameters())
                if selection_only
                else _text_patch_parameters(
                    resolved_target,
                    replacement=None,
                    expected_text=expected_text,
                    formatting=formatting,
                )
            ),
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        if selection_only:
            result = await self._executor.execute(
                metadata.FORMAT_TEXT_INTENT,
                inputs,
                None,
            )
        else:
            result = await self._executor.patch_text(
                metadata.FORMAT_TEXT_INTENT,
                inputs,
                TextPatchRequest(
                    target=resolved_target.to_live(),
                    expected_text=expected_text,
                    replacement="" if expected_text is None else expected_text,
                    formatting=formatting.to_live(),
                ),
            )
        return to_public_action_result(result, (), SELECTION_INPUT_ALIASES)
