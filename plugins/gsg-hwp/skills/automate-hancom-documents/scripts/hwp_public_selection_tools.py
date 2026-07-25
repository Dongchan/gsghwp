from __future__ import annotations

from typing import Final, final

import hwp_public_action_metadata as metadata
from hwp_operation_contract import (
    HwpOperateInputs,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs
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
    PublicTextPatchTarget,
)
from hwp_public_contract import PublicActionResult, to_public_action_result
from hwp_live_text_patch_contract import TextPatchRequest


_REPLACE_SELECTED_TEXT_INTENT: Final = "선택 텍스트 교체"


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
        document_path: str | None = None,
    ) -> PublicActionResult:
        resolved_target = PublicTextPatchTarget() if target is None else target
        parameters: dict[str, str | int | float | bool] = {
            "target_kind": resolved_target.kind,
            "replacement": replacement,
            "match_case": resolved_target.match_case,
        }
        if expected_text is not None:
            parameters["expected_text"] = expected_text
        if resolved_target.occurrence is not None:
            parameters["occurrence"] = resolved_target.occurrence
        if resolved_target.start is not None:
            parameters.update(
                {
                    "start_list": resolved_target.start.list_id,
                    "start_paragraph": resolved_target.start.paragraph,
                    "start_character": resolved_target.start.character,
                }
            )
        if resolved_target.end is not None:
            parameters.update(
                {
                    "end_list": resolved_target.end.list_id,
                    "end_paragraph": resolved_target.end.paragraph,
                    "end_character": resolved_target.end.character,
                }
            )
        if resolved_target.table_instance_id is not None:
            parameters["table_instance_id"] = resolved_target.table_instance_id
        if resolved_target.cell is not None:
            parameters["cell"] = resolved_target.cell.upper()
        if formatting is not None:
            parameters.update(formatting.to_parameters())
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
            ),
        )
        return to_public_action_result(result, (), SELECTION_INPUT_ALIASES)

    async def hwp_replace_selected_text(
        self,
        *,
        operation_id: PublicOperationId,
        replacement: PublicReplacementText,
        document_path: str | None = None,
    ) -> PublicActionResult:
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="document.replace_selection",
            target=HwpOperateTarget(kind="selection", binding="selection"),
            parameters={"replacement": replacement},
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.replace_selected_text(
            _REPLACE_SELECTED_TEXT_INTENT,
            inputs,
            replacement,
        )
        return to_public_action_result(result, (), SELECTION_INPUT_ALIASES)

    async def hwp_apply_style(
        self,
        *,
        operation_id: PublicOperationId,
        style_id: PublicStyleId,
        document_path: str | None = None,
    ) -> PublicActionResult:
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="style.apply",
            target=HwpOperateTarget(kind="selection", binding="selection"),
            recipe=HwpPriorityRecipeInputs(style_id=style_id),
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
        document_path: str | None = None,
    ) -> PublicActionResult:
        formatting = PublicTextFormattingInput(
            bold=bold,
            font_name=font_name,
            font_size_pt=font_size_pt,
            text_color=text_color,
            alignment=alignment,
            line_spacing=line_spacing,
        )
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="text.format",
            target=HwpOperateTarget(kind="selection", binding="selection"),
            parameters=dict(formatting.to_parameters()),
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(metadata.FORMAT_TEXT_INTENT, inputs, None)
        return to_public_action_result(result, (), SELECTION_INPUT_ALIASES)
