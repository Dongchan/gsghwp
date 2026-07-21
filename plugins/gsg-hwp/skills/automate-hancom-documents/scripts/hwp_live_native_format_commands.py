from __future__ import annotations

from dataclasses import dataclass
from hwp_live_native_action_models import (
    BooleanValue,
    CaptureTableCommand,
    CellCommand,
    IntegerValue,
    MillimeterValue,
    MergeCommand,
    NativeActionCommand,
    NativeSetter,
    ParameterActionCommand,
    RunCommand,
    SelectControlCommand,
)
from hwp_live_native_format_inputs import (
    MergeSpec,
    SplitSpec,
    TableFormatSpec,
    TextFormatSpec,
    table_cell_coordinate,
)
from hwp_live_native_format_target import ResolvedTable
from hwp_live_native_layout_format import cell_format_commands
from hwp_live_native_text_format import ParagraphFormatting, character_command, paragraph_command
from hwp_live_table_contract import TableCell
from hwp_live_native_table_layout import cell_address


@dataclass(frozen=True, slots=True)
class TextFormatCommandPlan:
    formatting: TextFormatSpec


@dataclass(frozen=True, slots=True)
class TableFormatCommandPlan:
    formatting: TableFormatSpec
    table: ResolvedTable


@dataclass(frozen=True, slots=True)
class MergeCommandPlan:
    merge: MergeSpec
    table: ResolvedTable


@dataclass(frozen=True, slots=True)
class SplitCommandPlan:
    split: SplitSpec
    table: ResolvedTable


type NativeFormatCommandPlan = (
    TextFormatCommandPlan
    | TableFormatCommandPlan
    | MergeCommandPlan
    | SplitCommandPlan
)


def _size_action(name: str, millimeters: float) -> ParameterActionCommand:
    return ParameterActionCommand(
        action="TablePropertyDialog",
        parameter_set="HShapeObject",
        setters=(
            NativeSetter("HSet/ShapeType", IntegerValue(3)),
            NativeSetter("HSet/ShapeCellSize", IntegerValue(1)),
            NativeSetter(f"ShapeTableCell/{name}", MillimeterValue(millimeters)),
        ),
    )


def _unlock_table_size_action() -> ParameterActionCommand:
    return ParameterActionCommand(
        action="TablePropertyDialog",
        parameter_set="HShapeObject",
        setters=(NativeSetter("ProtectSize", BooleanValue(False)),),
    )


def _select_range(
    first: str,
    move_action: str,
    count: int,
) -> tuple[NativeActionCommand, ...]:
    return (
        CellCommand(first),
        RunCommand("TableCellBlock"),
        RunCommand("TableCellBlockExtend"),
        *(RunCommand(move_action) for _ in range(count)),
    )


def build_native_format_commands(
    plan: NativeFormatCommandPlan,
) -> tuple[NativeActionCommand, ...]:
    if isinstance(plan, TextFormatCommandPlan):
        text = plan.formatting
        character = TableCell(
            bold=text.bold,
            font_name=text.font_name,
            font_size_pt=text.font_size_pt,
            text_color=text.text_color,
        )
        return tuple(
            command
            for command in (
                character_command(character),
                paragraph_command(
                    ParagraphFormatting(text.alignment, text.line_spacing)
                ),
            )
            if command is not None
        )
    if isinstance(plan, TableFormatCommandPlan):
        table = plan.table
        formatted = plan.formatting
        commands: list[NativeActionCommand] = [
            SelectControlCommand(plan.table.instance_id),
            CaptureTableCommand(),
        ]
        if (
            formatted.column_width_mm is not None
            or formatted.row_height_mm is not None
        ):
            commands.append(_unlock_table_size_action())
        row, column = table_cell_coordinate(formatted.cell)
        if formatted.column_width_mm is not None:
            assert table.rows is not None
            commands.extend(
                _select_range(cell_address(0, column - 1), "TableLowerCell", table.rows - 1)
            )
            commands.extend(
                (_size_action("Width", formatted.column_width_mm), RunCommand("Cancel"))
            )
        if formatted.row_height_mm is not None:
            assert table.columns is not None
            commands.extend(
                _select_range(cell_address(row - 1, 0), "TableRightCell", table.columns - 1)
            )
            commands.extend(
                (_size_action("Height", formatted.row_height_mm), RunCommand("Cancel"))
            )
        cell_commands = cell_format_commands(formatted.formatting)
        if cell_commands:
            commands.extend((CellCommand(formatted.cell), *cell_commands))
        return tuple(commands)
    if isinstance(plan, MergeCommandPlan):
        return (
            SelectControlCommand(plan.table.instance_id),
            CaptureTableCommand(),
            _unlock_table_size_action(),
            MergeCommand(plan.merge.start, plan.merge.end),
        )
    split = plan.split
    return (
        SelectControlCommand(plan.table.instance_id),
        CaptureTableCommand(),
        _unlock_table_size_action(),
        CellCommand(split.cell),
        ParameterActionCommand(
            "TableSplitCell",
            "HTableSplitCell",
            (
                NativeSetter("Cols", IntegerValue(split.columns)),
                NativeSetter("Rows", IntegerValue(split.rows)),
                NativeSetter(
                    "DistributeHeight",
                    IntegerValue(int(split.distribute_height)),
                ),
                NativeSetter("Merge", IntegerValue(int(split.merge))),
                NativeSetter("Mode2", IntegerValue(int(split.mode2))),
            ),
        ),
    )
