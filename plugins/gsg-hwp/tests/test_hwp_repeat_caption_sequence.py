from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_native_action_models import (  # noqa: E402
    CaptionCommand,
    InsertTextCommand,
    IntegerValue,
    NativeCharacterFormat,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSetter,
    MovePositionCommand,
    ParameterActionCommand,
    PasteTableCommand,
    RunCommand,
)
from hwp_live_native_template_repeat import (  # noqa: E402
    NativeCaptionProfile,
    caption_display_number,
    caption_has_display_number,
    clone_table_after_source_commands,
)
from hwp_live_template_repeat import (  # noqa: E402
    _caption_title_format_source,  # pyright: ignore[reportPrivateUsage]
)


def _caption_profile() -> NativeCaptionProfile:
    return NativeCaptionProfile(
        style_id=12,
        character_format=NativeCharacterFormat(
            face_name="함초롬바탕",
            height_hwpunit=1_000,
            bold=False,
            text_color=0,
        ),
        paragraph_format=NativeParagraphFormat(
            alignment=0,
            line_spacing=160,
            left_margin_hwpunit=0,
            right_margin_hwpunit=0,
            indentation_hwpunit=0,
            previous_spacing_hwpunit=0,
            next_spacing_hwpunit=0,
        ),
        format_source=NativePosition(31, 4, 0),
        automatic_number=True,
        display_number=11,
    )


def test_caption_style_copy_starts_at_title_instead_of_automatic_number() -> None:
    title = "가시권분석 -1"
    selection = NativeSelection(
        selected=True,
        start=NativePosition(31, 4, 0),
        end=NativePosition(31, 4, 27),
    )

    assert _caption_title_format_source(selection, title) == NativePosition(
        31,
        4,
        27 - len(title),
    )


def test_repeated_table_captions_advance_a_trailing_sequence_number() -> None:
    commands = clone_table_after_source_commands(
        "source-table",
        3,
        caption_title="예비조망점 가시권 분석표 -01",
        caption_profile=_caption_profile(),
    )

    titles = tuple(
        command.text for command in commands if isinstance(command, InsertTextCommand)
    )

    assert titles == (
        "예비조망점 가시권 분석표 -02",
        "예비조망점 가시권 분석표 -03",
        "예비조망점 가시권 분석표 -04",
    )
    assert not any(isinstance(command, CaptionCommand) for command in commands)
    actions = tuple(
        command.action for command in commands if isinstance(command, RunCommand)
    )
    assert actions.count("ShapeObjAttachCaption") == 3
    assert "ShapeObjDetachCaption" not in actions
    assert "Undo" not in actions
    assert actions.count("Delete") == 3
    assert "DeleteBack" not in actions
    assert "ShapeObjInsertCaptionNum" not in actions


def test_repeated_table_captions_preserve_non_sequence_titles() -> None:
    commands = clone_table_after_source_commands(
        "source-table",
        2,
        caption_title="건축개요 2024",
        caption_profile=_caption_profile(),
    )

    titles = tuple(
        command.text for command in commands if isinstance(command, InsertTextCommand)
    )

    assert titles == ("건축개요 2024", "건축개요 2024")
    assert not any(isinstance(command, CaptionCommand) for command in commands)


def test_repeated_table_captions_recreate_number_then_paste_source_text_shapes() -> None:
    commands = clone_table_after_source_commands(
        "source-table",
        2,
        caption_title="가시권분석 -1",
        caption_profile=_caption_profile(),
    )
    style_commands = tuple(
        command
        for command in commands
        if isinstance(command, ParameterActionCommand)
        and command.action == "ShapeCopyPaste"
    )
    number_commands = tuple(
        command
        for command in commands
        if isinstance(command, ParameterActionCommand)
        and command.action == "NewNumber"
    )

    assert commands[:3] == (
        MovePositionCommand(NativePosition(31, 4, 0)),
        style_commands[0],
        RunCommand("CloseEx"),
    )
    assert len(style_commands) == 3
    assert all(command.action == "ShapeCopyPaste" for command in style_commands)
    assert all(command.parameter_set == "HShapeCopyPaste" for command in style_commands)
    assert all(
        command.setters == (NativeSetter("Type", IntegerValue(2)),)
        for command in style_commands
    )
    assert len(number_commands) == 2
    assert tuple(command.parameter_set for command in number_commands) == (
        "HAutoNum",
        "HAutoNum",
    )
    assert tuple(command.setters for command in number_commands) == (
        (
            NativeSetter("NumType", IntegerValue(4)),
            NativeSetter("NewNumber", IntegerValue(12)),
        ),
        (
            NativeSetter("NumType", IntegerValue(4)),
            NativeSetter("NewNumber", IntegerValue(13)),
        ),
    )
    actions = tuple(
        command.action for command in commands if isinstance(command, RunCommand)
    )
    assert "ShapeObjDetachCaption" not in actions
    assert "Undo" not in actions
    assert actions.count("ShapeObjAttachCaption") == 2
    assert actions.count("Delete") == 2
    assert "DeleteBack" not in actions
    assert "ShapeObjInsertCaptionNum" not in actions
    style_indices = tuple(
        index
        for index, command in enumerate(commands)
        if isinstance(command, ParameterActionCommand)
        and command.action == "ShapeCopyPaste"
    )
    paste_indices = tuple(
        index
        for index, command in enumerate(commands)
        if isinstance(command, PasteTableCommand)
    )
    for copy_index, (style_index, paste_index) in enumerate(
        zip(style_indices[1:], paste_indices, strict=True),
        start=1,
    ):
        title = f"가시권분석 -{copy_index + 1}"
        assert commands[paste_index + 1 : style_index] == (
            RunCommand("ShapeObjAttachCaption"),
            number_commands[copy_index - 1],
            RunCommand("MoveParaEnd"),
            *(RunCommand("MoveSelLeft") for _ in "가시권분석 -1"),
            RunCommand("Delete"),
            InsertTextCommand(title),
            *(RunCommand("MoveSelLeft") for _ in title),
        )


def test_caption_number_detection_uses_the_rendered_caption_prefix() -> None:
    page_text = "본문\r\n<표 5.5.3-11>가시권분석 -1\r\n"

    assert caption_has_display_number(page_text, "가시권분석 -1")
    assert caption_display_number(page_text, "가시권분석 -1") == 11
    assert not caption_has_display_number("본문\r\n가시권분석 -1\r\n", "가시권분석 -1")
    assert caption_display_number("본문\r\n가시권분석 -1\r\n", "가시권분석 -1") is None
