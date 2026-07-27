from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

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
from hwp_live_native_text_format import (
    ParagraphFormatting,
    character_command,
    paragraph_command,
)
from hwp_live_table_contract import TableCell


FORMAT_PROTOCOL_COMMAND_LIMIT: Final = 20_000
# The native ceiling is an acceptance limit, not a latency target. Format calls
# stay at five percent of it because every parameter action also crosses one or
# more synchronous GetDefault/readback boundaries.
FORMAT_BUDGET_SAFETY_FACTOR: Final = 20
FORMAT_COMMAND_BUDGET: Final = (
    FORMAT_PROTOCOL_COMMAND_LIMIT // FORMAT_BUDGET_SAFETY_FACTOR
)
FORMAT_READBACK_BUDGET: Final = (
    FORMAT_PROTOCOL_COMMAND_LIMIT // FORMAT_BUDGET_SAFETY_FACTOR
)
# Keep the established axis-format call shape (up to 70 axis visits for a
# 50x20 table) while bounding the cell-visit work that the protocol command
# count cannot see. This is a regression-preserving work cap, not a latency
# claim: each topology rebuild visits the whole table.
FORMAT_TOPOLOGY_WORK_BUDGET: Final = FORMAT_COMMAND_BUDGET * 100


@dataclass(frozen=True, slots=True)
class TextFormatCommandPlan:
    formatting: TextFormatSpec


@dataclass(frozen=True, slots=True)
class AxisSizeTarget:
    key: str
    anchor: str
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CellRangeTarget:
    key: str
    anchor: str
    right_steps: int
    down_steps: int
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TableFormatCommandPlan:
    formatting: TableFormatSpec
    table: ResolvedTable
    cells: tuple[str, ...] = ()
    column_size_targets: tuple[AxisSizeTarget, ...] | None = None
    row_size_targets: tuple[AxisSizeTarget, ...] | None = None
    cell_geometry_targets: tuple[CellRangeTarget, ...] | None = None
    table_cell_count: int | None = None


@dataclass(frozen=True, slots=True)
class MergeCommandPlan:
    merge: MergeSpec
    table: ResolvedTable


@dataclass(frozen=True, slots=True)
class SplitCommandPlan:
    split: SplitSpec
    table: ResolvedTable


type NativeFormatCommandPlan = (
    TextFormatCommandPlan | TableFormatCommandPlan | MergeCommandPlan | SplitCommandPlan
)


@dataclass(frozen=True, slots=True)
class NativeFormatCommandGroup:
    key: str
    kind: Literal["column_width", "row_height", "cell_format", "cell_geometry"]
    addresses: tuple[str, ...]
    commands: tuple[NativeActionCommand, ...]
    readback_cost: int


@dataclass(frozen=True, slots=True)
class NativeFormatBatchGroup:
    group: NativeFormatCommandGroup
    start_command: int
    end_command: int


@dataclass(frozen=True, slots=True)
class NativeFormatCommandBatch:
    commands: tuple[NativeActionCommand, ...]
    groups: tuple[NativeFormatBatchGroup, ...]
    readback_cost: int
    topology_work: int

    def completed_group_keys(self, commands_completed: int) -> tuple[str, ...]:
        return tuple(
            item.group.key
            for item in self.groups
            if item.end_command <= commands_completed
        )

    def uncertain_addresses(self, commands_completed: int) -> tuple[str, ...]:
        return next(
            (
                item.group.addresses
                for item in self.groups
                if item.start_command < commands_completed < item.end_command
            ),
            (),
        )


@dataclass(frozen=True, slots=True)
class NativeFormatExecutionPlan:
    batches: tuple[NativeFormatCommandBatch, ...]
    address_requirements: tuple[tuple[str, frozenset[str]], ...]

    def affected_addresses(self, completed_group_keys: set[str]) -> tuple[str, ...]:
        return tuple(
            address
            for address, requirements in self.address_requirements
            if not requirements.isdisjoint(completed_group_keys)
        )

    def completed_addresses(self, completed_group_keys: set[str]) -> tuple[str, ...]:
        return tuple(
            address
            for address, requirements in self.address_requirements
            if requirements.issubset(completed_group_keys)
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


def _format_readback_cost(
    commands: tuple[NativeActionCommand, ...],
) -> int:
    cost = 0
    for command in commands:
        if not isinstance(command, ParameterActionCommand):
            continue
        cost += 1
        if command.action in {"CellFill", "CellBorder"}:
            cost += 1 + len(command.setters)
    return cost


_TOPOLOGY_PRESERVING_PARAMETER_ACTIONS: Final = frozenset(
    {
        ("Style", "HStyle"),
        ("CharShape", "HCharShape"),
        ("ParagraphShape", "HParaShape"),
        ("CellFill", "HCellBorderFill"),
        ("CellBorder", "HCellBorderFill"),
    }
)
_TOPOLOGY_PRESERVING_RUN_ACTIONS: Final = frozenset(
    {"TableVAlignTop", "TableVAlignCenter", "TableVAlignBottom"}
)


def _invalidates_cell_topology(command: NativeActionCommand) -> bool:
    if isinstance(command, ParameterActionCommand):
        return (
            command.action,
            command.parameter_set,
        ) not in _TOPOLOGY_PRESERVING_PARAMETER_ACTIONS
    if isinstance(command, RunCommand):
        return command.action not in _TOPOLOGY_PRESERVING_RUN_ACTIONS
    return True


def _table_cell_count(plan: TableFormatCommandPlan) -> int:
    if plan.table_cell_count is not None:
        return max(1, plan.table_cell_count)
    if plan.table.rows is not None and plan.table.columns is not None:
        return max(1, plan.table.rows * plan.table.columns)
    return max(1, len(plan.cells))


def _format_topology_work(
    commands: tuple[NativeActionCommand, ...],
    *,
    table_cell_count: int,
) -> int:
    topology_available = False
    work = 0
    for command in commands:
        if isinstance(command, (SelectControlCommand, CaptureTableCommand)):
            topology_available = False
        elif isinstance(command, CellCommand):
            if not topology_available:
                # CELL rebuilds the native table map after a geometry action:
                # M invalidation/re-entry pairs therefore cost M * C.
                work += table_cell_count
            topology_available = True
        elif _invalidates_cell_topology(command):
            topology_available = False
    return work


def _axis_size_targets(
    cells: tuple[str, ...],
    *,
    column: bool,
) -> tuple[AxisSizeTarget, ...]:
    grouped: dict[int, list[str]] = {}
    for cell in cells:
        row, cell_column = table_cell_coordinate(cell)
        axis = cell_column if column else row
        grouped.setdefault(axis, []).append(cell)
    label = "column" if column else "row"
    return tuple(
        AxisSizeTarget(
            key=f"{label}:{axis}",
            anchor=addresses[0],
            addresses=tuple(addresses),
        )
        for axis, addresses in grouped.items()
    )


def _cell_range_commands(
    target: CellRangeTarget,
    geometry_commands: tuple[NativeActionCommand, ...],
) -> tuple[NativeActionCommand, ...]:
    return (
        CellCommand(target.anchor),
        RunCommand("TableCellBlock"),
        RunCommand("TableCellBlockExtend"),
        *(RunCommand("TableRightCell") for _ in range(target.right_steps)),
        *(RunCommand("TableLowerCell") for _ in range(target.down_steps)),
        *geometry_commands,
        RunCommand("Cancel"),
    )


def _table_format_groups(
    plan: TableFormatCommandPlan,
) -> tuple[NativeFormatCommandGroup, ...]:
    formatted = plan.formatting
    cells = plan.cells or (() if formatted.cell is None else (formatted.cell,))
    groups: list[NativeFormatCommandGroup] = []
    if formatted.column_width_mm is not None:
        targets = (
            _axis_size_targets(cells, column=True)
            if plan.column_size_targets is None
            else plan.column_size_targets
        )
        for target in targets:
            commands: tuple[NativeActionCommand, ...] = (
                CellCommand(target.anchor),
                RunCommand("TableCellBlockCol"),
                _size_action("Width", formatted.column_width_mm),
                RunCommand("Cancel"),
            )
            groups.append(
                NativeFormatCommandGroup(
                    key=target.key,
                    kind="column_width",
                    addresses=target.addresses,
                    commands=commands,
                    readback_cost=_format_readback_cost(commands),
                )
            )
    if formatted.row_height_mm is not None:
        targets = (
            _axis_size_targets(cells, column=False)
            if plan.row_size_targets is None
            else plan.row_size_targets
        )
        for target in targets:
            commands = (
                CellCommand(target.anchor),
                RunCommand("TableCellBlockRow"),
                _size_action("Height", formatted.row_height_mm),
                RunCommand("Cancel"),
            )
            groups.append(
                NativeFormatCommandGroup(
                    key=target.key,
                    kind="row_height",
                    addresses=target.addresses,
                    commands=commands,
                    readback_cost=_format_readback_cost(commands),
                )
            )
    cell_commands = cell_format_commands(formatted.formatting)
    geometry_commands = tuple(
        command for command in cell_commands if _invalidates_cell_topology(command)
    )
    if cell_commands:
        # Only a topology-certified range may move geometry out of the legacy
        # per-cell sequence. Single-cell and complex-region fallbacks retain
        # the exact command order and result semantics.
        if not geometry_commands or not plan.cell_geometry_targets:
            for index, cell in enumerate(cells):
                commands = (CellCommand(cell), *cell_commands)
                groups.append(
                    NativeFormatCommandGroup(
                        key=f"cell:{index}:{cell}",
                        kind="cell_format",
                        addresses=(cell,),
                        commands=commands,
                        readback_cost=_format_readback_cost(commands),
                    )
                )
        else:
            regular_cell_commands = tuple(
                command
                for command in cell_commands
                if not _invalidates_cell_topology(command)
            )
            if regular_cell_commands:
                for index, cell in enumerate(cells):
                    commands = (CellCommand(cell), *regular_cell_commands)
                    groups.append(
                        NativeFormatCommandGroup(
                            key=f"cell:{index}:{cell}",
                            kind="cell_format",
                            addresses=(cell,),
                            commands=commands,
                            readback_cost=_format_readback_cost(commands),
                        )
                    )
            for target in plan.cell_geometry_targets:
                commands = _cell_range_commands(target, geometry_commands)
                groups.append(
                    NativeFormatCommandGroup(
                        key=target.key,
                        kind="cell_geometry",
                        addresses=target.addresses,
                        commands=commands,
                        readback_cost=_format_readback_cost(commands),
                    )
                )
    return tuple(groups)


def _table_format_batch(
    plan: TableFormatCommandPlan,
    groups: tuple[NativeFormatCommandGroup, ...],
    *,
    preserve_empty_size_unlock: bool = False,
) -> NativeFormatCommandBatch:
    commands: list[NativeActionCommand] = [
        SelectControlCommand(plan.table.instance_id),
        CaptureTableCommand(),
    ]
    size_groups = any(group.kind in {"column_width", "row_height"} for group in groups)
    formatted = plan.formatting
    if size_groups or (
        preserve_empty_size_unlock
        and (
            formatted.column_width_mm is not None or formatted.row_height_mm is not None
        )
    ):
        commands.append(_unlock_table_size_action())
    batch_groups: list[NativeFormatBatchGroup] = []
    for group in groups:
        start = len(commands)
        commands.extend(group.commands)
        batch_groups.append(NativeFormatBatchGroup(group, start, len(commands)))
    native_commands = tuple(commands)
    return NativeFormatCommandBatch(
        native_commands,
        tuple(batch_groups),
        _format_readback_cost(native_commands),
        _format_topology_work(
            native_commands,
            table_cell_count=_table_cell_count(plan),
        ),
    )


def _address_requirements(
    cells: tuple[str, ...],
    groups: tuple[NativeFormatCommandGroup, ...],
) -> tuple[tuple[str, frozenset[str]], ...]:
    requirements = {cell: set[str]() for cell in dict.fromkeys(cells)}
    for group in groups:
        for address in group.addresses:
            if address in requirements:
                requirements[address].add(group.key)
    return tuple(
        (address, frozenset(required))
        for address, required in requirements.items()
        if required
    )


def build_native_format_execution_plan(
    plan: TableFormatCommandPlan,
) -> NativeFormatExecutionPlan:
    formatted = plan.formatting
    cells = plan.cells or (() if formatted.cell is None else (formatted.cell,))
    groups = _table_format_groups(plan)
    canonical = _table_format_batch(
        plan,
        groups,
        preserve_empty_size_unlock=True,
    )
    requirements = _address_requirements(cells, groups)
    if (
        len(canonical.commands) <= FORMAT_COMMAND_BUDGET
        and canonical.readback_cost <= FORMAT_READBACK_BUDGET
        and canonical.topology_work <= FORMAT_TOPOLOGY_WORK_BUDGET
    ) or not groups:
        return NativeFormatExecutionPlan((canonical,), requirements)

    batches: list[NativeFormatCommandBatch] = []
    current: list[NativeFormatCommandGroup] = []
    for group in groups:
        candidate = _table_format_batch(plan, (*current, group))
        if current and (
            len(candidate.commands) > FORMAT_COMMAND_BUDGET
            or candidate.readback_cost > FORMAT_READBACK_BUDGET
            or candidate.topology_work > FORMAT_TOPOLOGY_WORK_BUDGET
        ):
            batches.append(_table_format_batch(plan, tuple(current)))
            current = [group]
            candidate = _table_format_batch(plan, (group,))
        else:
            current.append(group)
        if (
            len(candidate.commands) > FORMAT_COMMAND_BUDGET
            or candidate.readback_cost > FORMAT_READBACK_BUDGET
            or candidate.topology_work > FORMAT_TOPOLOGY_WORK_BUDGET
        ):
            raise ValueError("단일 표 서식 명령 그룹이 전용 호출 budget을 초과합니다")
    if current:
        batches.append(_table_format_batch(plan, tuple(current)))
    return NativeFormatExecutionPlan(tuple(batches), requirements)


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
        groups = _table_format_groups(plan)
        return _table_format_batch(
            plan,
            groups,
            preserve_empty_size_unlock=True,
        ).commands
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
                NativeSetter(
                    "Mode2",
                    IntegerValue(int(split.split_mode == "existing_grid")),
                ),
            ),
        ),
    )
