from __future__ import annotations

from typing import final

import hwp_public_action_metadata as metadata
from hwp_live_contract import MutationResult
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
    PublicLineSpacing,
    PublicReplacementText,
    PublicSelectionExecutor,
    PublicStyleId,
    PublicTextAlignment,
    PublicTextColor,
    PublicTextFormattingInput,
    new_public_request_id,
)
from hwp_public_contract import PublicActionResult, to_public_action_result


@final
class HwpPublicSelectionTools:
    __slots__ = ("_executor",)

    def __init__(self, executor: PublicSelectionExecutor) -> None:
        self._executor = executor

    async def hwp_replace_selected_text(
        self,
        *,
        replacement: PublicReplacementText,
        document_path: str | None = None,
    ) -> MutationResult:
        return await self._executor.replace_selected_text(document_path, replacement)

    async def hwp_apply_style(
        self,
        *,
        style_id: PublicStyleId,
        document_path: str | None = None,
    ) -> PublicActionResult:
        inputs = HwpOperateInputs(
            request_id=new_public_request_id(),
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
            request_id=new_public_request_id(),
            document=document_path,
            operation="text.format",
            target=HwpOperateTarget(kind="selection", binding="selection"),
            parameters=dict(formatting.to_parameters()),
            policy=HwpOperatePolicy(ambiguity="return_candidates"),
            postconditions=HwpOperatePostconditions(verify_structure=True),
        )
        result = await self._executor.execute(metadata.FORMAT_TEXT_INTENT, inputs, None)
        return to_public_action_result(result, (), SELECTION_INPUT_ALIASES)
