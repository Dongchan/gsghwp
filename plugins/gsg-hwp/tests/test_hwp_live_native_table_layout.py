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

from hwp_live_contract import LayoutPlan  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    BooleanValue,
    CaptionCommand,
    IntegerValue,
    MergeCommand,
    NativeActionCommand,
    NativeSetter,
    ParameterActionCommand,
)
from hwp_live_native_layout import (  # noqa: E402
    NativeLayoutContext,
    build_native_layout_request,
)
from hwp_live_table_contract import TableBlock, TableCell, TableMerge  # noqa: E402


_HEADER_ACTION = ParameterActionCommand(
    action="TablePropertyDialog",
    parameter_set="HShapeObject",
    setters=(NativeSetter("ShapeTableCell/Header", BooleanValue(True)),),
)


def _commands(*, repeat_header: bool) -> tuple[NativeActionCommand, ...]:
    table = TableBlock(
        kind="table",
        base_style_id=0,
        caption="표 제목",
        caption_style_id=1,
        rows=(
            (
                TableCell(text="머리글", bold=True),
                TableCell(),
            ),
            (
                TableCell(text="값"),
                TableCell(text="설명"),
            ),
        ),
        repeat_header=repeat_header,
        merges=(TableMerge(row=0, column=0, row_span=1, column_span=2),),
    )
    request = build_native_layout_request(
        NativeLayoutContext(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            style_ids=(),
        ),
        LayoutPlan(blocks=(table,)),
        {},
    )
    return request.commands


def test_repeat_header_is_not_applied_until_a_working_command_exists() -> None:
    """repeat_header 는 현재 의도적으로 적용하지 않는다.

    라이브 검증(LV9)에서 45x4 표 3/3 회 모두 2쪽이 완전히 비고 데이터 행이
    렌더되지 않았다. 다른 TablePropertyDialog 명령이 함께 보내는 문맥 setter
    (HSet/ShapeType, HSet/ShapeCellSize) 없이 ShapeTableCell/Header 만 보내
    표 속성이 어긋나는 것으로 보인다. 문맥을 갖춘 형태로 다시 만들기 전까지
    명령을 내보내지 않는다.
    """
    enabled = _commands(repeat_header=True)
    disabled = _commands(repeat_header=False)

    header_setters = [
        setter
        for command in enabled
        if isinstance(command, ParameterActionCommand)
        for setter in command.setters
        if setter.path.endswith("Header")
    ]
    assert header_setters == []
    assert enabled == disabled

def test_repeat_header_false_emits_no_header_action_or_extra_command() -> None:
    without_header = _commands(repeat_header=False)
    with_header = _commands(repeat_header=True)

    assert _HEADER_ACTION not in without_header
    assert tuple(command for command in with_header if command != _HEADER_ACTION) == (
        without_header
    )


def test_repeat_header_keeps_table_shape_format_merge_and_caption_commands() -> None:
    commands = _commands(repeat_header=True)
    table_create = next(
        command
        for command in commands
        if isinstance(command, ParameterActionCommand)
        and command.action == "TableCreate"
    )

    assert NativeSetter("Rows", IntegerValue(2)) in table_create.setters
    assert NativeSetter("Cols", IntegerValue(2)) in table_create.setters
    assert MergeCommand("A1", "B1") in commands
    assert any(
        isinstance(command, ParameterActionCommand)
        and command.action == "CharShape"
        and NativeSetter("Bold", BooleanValue(True)) in command.setters
        for command in commands
    )
    assert any(
        isinstance(command, CaptionCommand) and command.title == "표 제목"
        for command in commands
    )
