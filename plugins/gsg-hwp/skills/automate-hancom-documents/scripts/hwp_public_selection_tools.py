from __future__ import annotations

from typing import Final, final

import hwp_public_action_metadata as metadata
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
    PublicTextPatchPosition,
    PublicTextPatchTarget,
)
from hwp_public_contract import PublicActionResult, to_public_action_result
from hwp_live_text_patch_contract import TextPatchRequest


_REPLACE_SELECTED_TEXT_INTENT: Final = "선택 텍스트 교체"


def _text_patch_parameters(
    target: PublicTextPatchTarget,
    *,
    replacement: str | None,
    expected_text: str | None,
    formatting: PublicTextFormattingInput | None,
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
        document_path: str | None = None,
    ) -> PublicActionResult:
        resolved_target = PublicTextPatchTarget() if target is None else target
        parameters = _text_patch_parameters(
            resolved_target,
            replacement=replacement,
            expected_text=expected_text,
            formatting=formatting,
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
            ),
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
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_path,
            operation="text.format",
            target=(
                HwpOperateTarget(kind="selection", binding="selection")
                if resolved_target.kind == "current" and expected_text is None
                else None
            ),
            parameters=_text_patch_parameters(
                resolved_target,
                replacement=None,
                expected_text=expected_text,
                formatting=formatting,
            ),
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        if resolved_target.kind == "current" and expected_text is None:
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
