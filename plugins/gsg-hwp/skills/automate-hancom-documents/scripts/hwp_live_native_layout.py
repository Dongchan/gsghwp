from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from hwp_errors import HwpLiveError
from hwp_live_contract import (
    ImageBlock,
    LayoutPlan,
    PageBreakBlock,
    ParagraphBlock,
)
from hwp_live_native_action_models import (
    InsertPictureCommand,
    InsertTextCommand,
    MoveDocumentEndCommand,
    NativeActionCommand,
    NativeActionRequest,
    NativeCharacterFormat,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    RunCommand,
)
from hwp_live_native_table_layout import table_commands
from hwp_live_native_text_format import (
    ParagraphFormatting,
    character_command,
    paragraph_command,
    style_command,
)
from hwp_live_table_contract import TableBlock


@dataclass(frozen=True, slots=True)
class NativeLayoutContext:
    document_id: int
    full_name: str
    style_ids: tuple[tuple[str, int], ...]
    text_formats: tuple[
        tuple[str, NativeCharacterFormat, NativeParagraphFormat], ...
    ] = ()
    caption_format_sources: tuple[tuple[str, NativePosition], ...] = ()
    expected_cursor: NativePosition | None = None
    expected_selection: NativeSelection | None = None


def build_native_layout_request(
    context: NativeLayoutContext,
    plan: LayoutPlan,
    assets: Mapping[Path, Path],
) -> NativeActionRequest:
    commands: list[NativeActionCommand] = []
    if plan.target == "document_end":
        commands.append(MoveDocumentEndCommand())
    if plan.replace_selection:
        if context.expected_selection is None or not context.expected_selection.selected:
            raise HwpLiveError("선택 영역 교체에는 선택 상태 스냅샷이 필요합니다")
        commands.append(RunCommand("Delete"))
    styles = dict(context.style_ids)
    text_formats = {
        name: (character, paragraph)
        for name, character, paragraph in context.text_formats
    }
    caption_format_sources = dict(context.caption_format_sources)
    for block_index, block in enumerate(plan.blocks):
        match block:
            case ParagraphBlock():
                if block.style_id is not None:
                    commands.append(style_command(block.style_id))
                character = character_command(block)
                if character is not None:
                    commands.append(character)
                paragraph = paragraph_command(
                    ParagraphFormatting(
                        alignment=block.alignment,
                        line_spacing=block.line_spacing_percent,
                        before_mm=block.space_before_mm,
                        after_mm=block.space_after_mm,
                        left_mm=block.left_margin_mm,
                        right_mm=block.right_margin_mm,
                        indentation_mm=block.indentation_mm,
                    )
                )
                if paragraph is not None:
                    commands.append(paragraph)
                commands.append(InsertTextCommand(block.text))
                if block_index + 1 < len(plan.blocks):
                    commands.append(RunCommand("BreakPara"))
                continue
            case TableBlock():
                commands.extend(
                    table_commands(
                        block,
                        assets,
                        styles,
                        text_formats,
                        caption_format_sources,
                    )
                )
                continue
            case ImageBlock():
                try:
                    image = assets[block.path]
                except KeyError as error:
                    raise HwpLiveError(f"그림 파일이 준비되지 않았습니다: {block.path}") from error
                paragraph = paragraph_command(
                    ParagraphFormatting(alignment=block.alignment)
                )
                if paragraph is not None:
                    commands.append(paragraph)
                commands.extend(
                    (
                        InsertPictureCommand(
                            path=image,
                            width_mm=block.width_mm,
                            height_mm=block.height_mm,
                        ),
                        RunCommand("BreakPara"),
                    )
                )
                if block.caption is not None:
                    if block.caption_style_id is not None:
                        commands.append(style_command(block.caption_style_id))
                    caption_paragraph = paragraph_command(
                        ParagraphFormatting(alignment="center")
                    )
                    if caption_paragraph is not None:
                        commands.append(caption_paragraph)
                    commands.extend(
                        (
                            InsertTextCommand(block.caption),
                            RunCommand("BreakPara"),
                        )
                    )
                continue
            case PageBreakBlock():
                commands.append(RunCommand("BreakPage"))
                continue
        assert_never(block)
    return NativeActionRequest(
        document_id=context.document_id,
        full_name=context.full_name,
        commands=tuple(commands),
        expected_cursor=context.expected_cursor,
        expected_selection=context.expected_selection,
    )
