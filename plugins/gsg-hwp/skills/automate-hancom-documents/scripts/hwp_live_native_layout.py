from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, assert_never

from hwp_errors import HwpLiveError
from hwp_live_contract import (
    ImageBlock,
    LayoutPlan,
    PageBreakBlock,
    ParagraphBlock,
)
from hwp_live_native_action_models import (
    CaptionCommand,
    CellCommand,
    IntegerValue,
    InsertPictureCommand,
    InsertTextCommand,
    LeaveTableCommand,
    MergeCommand,
    MoveDocumentEndCommand,
    MovePageCommand,
    NativeActionCommand,
    NativeActionRequest,
    NativeCharacterFormat,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    ParameterActionCommand,
    RunCommand,
)
from hwp_live_native_layout_format import (
    cell_padding_ranges,
    is_padding_command,
)
from hwp_live_native_table_layout import table_commands
from hwp_live_native_text_format import (
    ParagraphFormatting,
    character_command,
    paragraph_command,
    style_command,
)
from hwp_live_table_contract import TableBlock
from hwp_table_address import cell_address
from hwp_reference_layout_contract import ReferenceLayoutBlock
from hwp_reference_layout_evidence import prepare_reference_layout
from hwp_reference_layout_geometry import SectionPageGeometry
from hwp_reference_layout_native import compile_reference_layout_command
from hwp_reference_layout_patch import (
    ReferenceLayoutPatchBlock,
    compile_reference_layout_patch_command,
)


LAYOUT_COMMAND_LIMIT: Final = 20_000
# This is the V28 regression ceiling, not a wall-clock latency guarantee.
LAYOUT_TOPOLOGY_WORK_BUDGET: Final = 100_000


@dataclass(frozen=True, slots=True)
class NativeLayoutContext:
    document_id: int
    full_name: str
    style_ids: tuple[tuple[str, int], ...]
    text_formats: tuple[
        tuple[str, NativeCharacterFormat, NativeParagraphFormat], ...
    ] = ()
    caption_format_sources: tuple[tuple[str, NativePosition], ...] = ()
    page_count: int | None = None
    page_geometry: SectionPageGeometry | None = None
    page_number: int = 1
    base_style_id: int = 0
    expected_cursor: NativePosition | None = None
    expected_selection: NativeSelection | None = None


@dataclass(frozen=True, slots=True)
class NativeLayoutCommandGroup:
    key: int
    commands: tuple[NativeActionCommand, ...]
    table_index: int | None
    table_cell_count: int
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NativeLayoutBatchGroup:
    group: NativeLayoutCommandGroup
    start_command: int
    end_command: int


@dataclass(frozen=True, slots=True)
class NativeLayoutBatch:
    request: NativeActionRequest
    groups: tuple[NativeLayoutBatchGroup, ...]
    topology_work: int

    def completed_group_keys(self, commands_completed: int) -> tuple[int, ...]:
        return tuple(
            item.group.key
            for item in self.groups
            if item.end_command <= commands_completed
        )


@dataclass(frozen=True, slots=True)
class NativeLayoutExecutionPlan:
    batches: tuple[NativeLayoutBatch, ...]
    address_requirements: tuple[
        tuple[int, str, frozenset[int]],
        ...,
    ]

    def completed_cells(
        self,
        completed_group_keys: set[int],
    ) -> tuple[tuple[int, str], ...]:
        return tuple(
            (table_index, address)
            for table_index, address, requirements in self.address_requirements
            if requirements.issubset(completed_group_keys)
        )

    def completed_addresses(self, completed_group_keys: set[int]) -> tuple[str, ...]:
        completed = self.completed_cells(completed_group_keys)
        table_indexes = {table_index for table_index, _, _ in self.address_requirements}
        if len(table_indexes) <= 1:
            return tuple(address for _, address in completed)
        return tuple(
            f"table[{table_index + 1}]:{address}" for table_index, address in completed
        )


def _optimized_table_commands(
    block: TableBlock,
    commands: tuple[NativeActionCommand, ...],
) -> tuple[NativeActionCommand, ...]:
    ranges = cell_padding_ranges(block)
    if not ranges:
        return commands
    grouped_addresses = {address for target in ranges for address in target.addresses}
    rows = len(block.rows)
    columns = len(block.rows[0])
    geometry_cells = (columns if block.column_widths_mm is not None else 0) + (
        rows if block.row_heights_mm is not None else 0
    )
    cell_positions = tuple(
        index
        for index, command in enumerate(commands)
        if isinstance(command, CellCommand)
    )
    cell_count = rows * columns
    main_positions = cell_positions[geometry_cells : geometry_cells + cell_count]
    expected_addresses = tuple(
        cell_address(row, column) for row in range(rows) for column in range(columns)
    )
    main_cells = tuple(commands[index] for index in main_positions)
    if (
        len(main_positions) != cell_count
        or not all(isinstance(command, CellCommand) for command in main_cells)
        or tuple(
            command.address
            for command in main_cells
            if isinstance(command, CellCommand)
        )
        != expected_addresses
    ):
        return commands

    if geometry_cells:
        next_cell = geometry_cells + cell_count
        if next_cell >= len(cell_positions):
            return commands
        main_end = cell_positions[next_cell]
    else:
        last_main = main_positions[-1]
        main_end = next(
            (
                index
                for index in range(last_main + 1, len(commands))
                if isinstance(
                    commands[index],
                    (MergeCommand, CaptionCommand, LeaveTableCommand),
                )
            ),
            len(commands),
        )
    insertion = main_positions[0]

    removals: set[int] = set()
    for offset, start in enumerate(main_positions):
        address = expected_addresses[offset]
        if address not in grouped_addresses:
            continue
        end = (
            main_positions[offset + 1] if offset + 1 < len(main_positions) else main_end
        )
        padding_indexes = tuple(
            index
            for index in range(start + 1, end)
            if is_padding_command(commands[index])
        )
        if len(padding_indexes) != 1:
            return commands
        removals.add(padding_indexes[0])

    grouped_commands = tuple(
        command for target in ranges for command in target.commands
    )
    optimized: list[NativeActionCommand] = []
    for index, command in enumerate(commands):
        if index == insertion:
            optimized.extend(grouped_commands)
        if index not in removals:
            optimized.append(command)
    if insertion == len(commands):
        optimized.extend(grouped_commands)
    return tuple(optimized)


def build_native_layout_request(
    context: NativeLayoutContext,
    plan: LayoutPlan,
    assets: Mapping[Path, Path],
) -> NativeActionRequest:
    commands: list[NativeActionCommand] = []
    match plan.target:
        case "current":
            pass
        case "document_end":
            commands.append(MoveDocumentEndCommand())
        case "after_page":
            if plan.page is None:
                raise HwpLiveError("쪽 다음 삽입에는 page가 필요합니다")
            if context.page_count is not None and plan.page > context.page_count:
                raise HwpLiveError("삽입 기준 쪽이 현재 문서 쪽 수를 초과합니다")
            if context.page_count is not None and plan.page == context.page_count:
                commands.extend(
                    (
                        MovePageCommand(plan.page),
                        RunCommand("MovePageEnd"),
                        RunCommand("BreakPage"),
                    )
                )
            else:
                commands.extend(
                    (
                        MovePageCommand(plan.page + 1),
                        RunCommand("MovePageBegin"),
                        RunCommand("BreakPage"),
                        MovePageCommand(plan.page + 1),
                        RunCommand("MovePageBegin"),
                    )
                )
    if plan.replace_selection:
        if (
            context.expected_selection is None
            or not context.expected_selection.selected
        ):
            raise HwpLiveError("선택 영역 교체에는 선택 상태 스냅샷이 필요합니다")
        commands.append(RunCommand("Delete"))
    styles = dict(context.style_ids)
    text_formats = {
        name: (character, paragraph)
        for name, character, paragraph in context.text_formats
    }
    caption_format_sources = dict(context.caption_format_sources)
    layout_page_number = context.page_number
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
                    _optimized_table_commands(
                        block,
                        table_commands(
                            block,
                            assets,
                            styles,
                            text_formats,
                            caption_format_sources,
                        ),
                    )
                )
                continue
            case ReferenceLayoutBlock():
                if context.page_geometry is None:
                    raise HwpLiveError(
                        "참조 이미지 레이아웃에는 현재 구역 용지 정보가 필요합니다"
                    )
                commands.append(
                    compile_reference_layout_command(
                        prepare_reference_layout(block),
                        context.page_geometry,
                        page_number=layout_page_number,
                        base_style_id=context.base_style_id,
                    )
                )
                continue
            case ReferenceLayoutPatchBlock():
                if context.page_geometry is None:
                    raise HwpLiveError(
                        "참조 이미지 부분 보정에는 현재 구역 용지 정보가 필요합니다"
                    )
                commands.append(
                    compile_reference_layout_patch_command(
                        block,
                        context.page_geometry,
                        page_number=context.page_number,
                    )
                )
                continue
            case ImageBlock():
                try:
                    image = assets[block.path]
                except KeyError as error:
                    raise HwpLiveError(
                        f"그림 파일이 준비되지 않았습니다: {block.path}"
                    ) from error
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
                    commands.append(InsertTextCommand(block.caption))
                    if block_index + 1 < len(plan.blocks):
                        commands.append(RunCommand("BreakPara"))
                continue
            case PageBreakBlock():
                commands.append(RunCommand("BreakPage"))
                layout_page_number += 1
                continue
        assert_never(block)
    return NativeActionRequest(
        document_id=context.document_id,
        full_name=context.full_name,
        commands=tuple(commands),
        expected_cursor=context.expected_cursor,
        expected_selection=context.expected_selection,
    )


def _table_creation_cell_count(command: NativeActionCommand) -> int | None:
    if (
        not isinstance(command, ParameterActionCommand)
        or command.action != "TableCreate"
        or command.parameter_set != "HTableCreation"
    ):
        return None
    values = {
        setter.path: setter.value.value
        for setter in command.setters
        if isinstance(setter.value, IntegerValue)
    }
    rows = values.get("Rows")
    columns = values.get("Cols")
    if rows is None or columns is None or rows < 1 or columns < 1:
        return None
    return rows * columns


def _cell_coordinate(address: str) -> tuple[int, int]:
    split = 0
    while split < len(address) and address[split].isalpha():
        split += 1
    letters = address[:split].upper()
    digits = address[split:]
    if not letters or not digits or not digits.isdecimal():
        raise HwpLiveError(f"표 셀 주소가 올바르지 않습니다: {address}")
    column = 0
    for letter in letters:
        column = column * 26 + ord(letter) - ord("A") + 1
    row = int(digits)
    if row < 1:
        raise HwpLiveError(f"표 셀 주소가 올바르지 않습니다: {address}")
    return row - 1, column - 1


def _rectangle_addresses(first: str, second: str) -> tuple[str, ...]:
    first_row, first_column = _cell_coordinate(first)
    second_row, second_column = _cell_coordinate(second)
    top, bottom = sorted((first_row, second_row))
    left, right = sorted((first_column, second_column))
    return tuple(
        cell_address(row, column)
        for row in range(top, bottom + 1)
        for column in range(left, right + 1)
    )


def _group_addresses(
    commands: tuple[NativeActionCommand, ...],
) -> tuple[str, ...]:
    addresses: list[str] = []
    first = commands[0] if commands else None
    if isinstance(first, CellCommand):
        selected = (first.address,)
        if (
            len(commands) >= 3
            and commands[1] == RunCommand("TableCellBlock")
            and commands[2] == RunCommand("TableCellBlockExtend")
        ):
            right_steps = sum(
                command == RunCommand("TableRightCell") for command in commands[3:]
            )
            down_steps = sum(
                command == RunCommand("TableLowerCell") for command in commands[3:]
            )
            row, column = _cell_coordinate(first.address)
            selected = _rectangle_addresses(
                first.address,
                cell_address(row + down_steps, column + right_steps),
            )
        addresses.extend(selected)
    for command in commands:
        if isinstance(command, MergeCommand):
            addresses.extend(_rectangle_addresses(command.first, command.second))
    return tuple(dict.fromkeys(addresses))


def _layout_command_groups(
    request: NativeActionRequest,
) -> tuple[NativeLayoutCommandGroup, ...]:
    groups: list[NativeLayoutCommandGroup] = []
    current: list[NativeActionCommand] = []
    table_index: int | None = None
    table_cell_count = 0
    next_table_index = 0

    def flush() -> None:
        if not current:
            return
        native_commands = tuple(current)
        groups.append(
            NativeLayoutCommandGroup(
                key=len(groups),
                commands=native_commands,
                table_index=table_index,
                table_cell_count=table_cell_count,
                addresses=_group_addresses(native_commands),
            )
        )
        current.clear()

    for command in request.commands:
        created_cells = _table_creation_cell_count(command)
        if created_cells is not None:
            table_index = next_table_index
            next_table_index += 1
            table_cell_count = created_cells
            current.append(command)
            continue
        if isinstance(command, CellCommand):
            flush()
            current.append(command)
            continue
        current.append(command)
        if isinstance(command, LeaveTableCommand):
            flush()
            table_index = None
            table_cell_count = 0
    flush()
    return tuple(groups)


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


@dataclass(frozen=True, slots=True)
class _TopologyWorkState:
    topology_available: bool = False
    topology_table: int | None = None
    work: int = 0


def _advance_topology_work(
    state: _TopologyWorkState,
    group: NativeLayoutCommandGroup,
) -> _TopologyWorkState:
    topology_available = state.topology_available
    topology_table = state.topology_table
    work = state.work
    for command in group.commands:
        if isinstance(command, CellCommand):
            if not topology_available or topology_table != group.table_index:
                work += group.table_cell_count
            topology_available = True
            topology_table = group.table_index
            continue
        if isinstance(command, MergeCommand):
            if not topology_available or topology_table != group.table_index:
                work += group.table_cell_count
            topology_available = False
            topology_table = group.table_index
            continue
        if isinstance(command, ParameterActionCommand):
            if (
                command.action,
                command.parameter_set,
            ) not in _TOPOLOGY_PRESERVING_PARAMETER_ACTIONS:
                topology_available = False
            continue
        if isinstance(command, RunCommand):
            if command.action not in _TOPOLOGY_PRESERVING_RUN_ACTIONS:
                topology_available = False
            continue
        if isinstance(command, LeaveTableCommand):
            topology_available = False
            topology_table = None
    return _TopologyWorkState(topology_available, topology_table, work)


def _layout_topology_work(
    groups: tuple[NativeLayoutCommandGroup, ...],
) -> int:
    state = _TopologyWorkState()
    for group in groups:
        state = _advance_topology_work(state, group)
    return state.work


def _batch_from_groups(
    request: NativeActionRequest,
    groups: tuple[NativeLayoutCommandGroup, ...],
    *,
    first: bool,
    preserve_request: bool = False,
) -> NativeLayoutBatch:
    commands: list[NativeActionCommand] = []
    batch_groups: list[NativeLayoutBatchGroup] = []
    for group in groups:
        start = len(commands)
        commands.extend(group.commands)
        batch_groups.append(NativeLayoutBatchGroup(group, start, len(commands)))
    batch_request = (
        request
        if preserve_request
        else replace(
            request,
            commands=tuple(commands),
            expected_cursor=request.expected_cursor if first else None,
            expected_selection=request.expected_selection if first else None,
            atomic=request.atomic if first else False,
        )
    )
    return NativeLayoutBatch(
        request=batch_request,
        groups=tuple(batch_groups),
        topology_work=_layout_topology_work(groups),
    )


def _address_requirements(
    groups: tuple[NativeLayoutCommandGroup, ...],
) -> tuple[tuple[int, str, frozenset[int]], ...]:
    order: list[tuple[int, str]] = []
    requirements: dict[tuple[int, str], set[int]] = {}
    for group in groups:
        if group.table_index is None:
            continue
        for address in group.addresses:
            key = (group.table_index, address)
            if key not in requirements:
                order.append(key)
                requirements[key] = set()
            requirements[key].add(group.key)
    return tuple(
        (table_index, address, frozenset(requirements[(table_index, address)]))
        for table_index, address in order
    )


def build_native_layout_execution_plan(
    request: NativeActionRequest,
) -> NativeLayoutExecutionPlan:
    groups = _layout_command_groups(request)
    requirements = _address_requirements(groups)
    canonical_work = _layout_topology_work(groups)
    # LAYOUT_COMMAND_LIMIT mirrors kMaximumCommands in ActionProtocol.cpp:21.
    # The parser there rejects the whole script (ActionProtocol.cpp:537) without
    # looking at `atomic`, so an atomic request over the limit can only come
    # back as BAD_REQUEST after a full round trip. Splitting it is not an
    # option — it is one rollback unit — so fail here, where the cause is still
    # nameable. Non-atomic requests keep falling through to the splitter.
    if request.atomic and len(request.commands) > LAYOUT_COMMAND_LIMIT:
        raise HwpLiveError(
            "레이아웃 요청 명령이 네이티브 한계 20000개를 넘었습니다"
            + f" (요청 {len(request.commands)}개)."
            + " 원자 실행을 유지하려면 블록 수를 줄여 다시 요청하세요"
        )
    if (
        request.atomic
        or (
            len(request.commands) <= LAYOUT_COMMAND_LIMIT
            and canonical_work <= LAYOUT_TOPOLOGY_WORK_BUDGET
        )
        or not groups
    ):
        return NativeLayoutExecutionPlan(
            (
                _batch_from_groups(
                    request,
                    groups,
                    first=True,
                    preserve_request=True,
                ),
            ),
            requirements,
        )

    grouped_batches: list[tuple[NativeLayoutCommandGroup, ...]] = []
    current: list[NativeLayoutCommandGroup] = []
    current_commands = 0
    current_state = _TopologyWorkState()
    for group in groups:
        candidate_commands = current_commands + len(group.commands)
        candidate_state = _advance_topology_work(current_state, group)
        if current and (
            candidate_commands > LAYOUT_COMMAND_LIMIT
            or candidate_state.work > LAYOUT_TOPOLOGY_WORK_BUDGET
        ):
            grouped_batches.append(tuple(current))
            current = [group]
            candidate_commands = len(group.commands)
            candidate_state = _advance_topology_work(_TopologyWorkState(), group)
        else:
            current.append(group)
        current_commands = candidate_commands
        current_state = candidate_state
        if (
            candidate_commands > LAYOUT_COMMAND_LIMIT
            or candidate_state.work > LAYOUT_TOPOLOGY_WORK_BUDGET
        ):
            raise HwpLiveError(
                "단일 레이아웃 명령 그룹이 네이티브 호출 budget을 초과합니다"
            )
    if current:
        grouped_batches.append(tuple(current))
    return NativeLayoutExecutionPlan(
        tuple(
            _batch_from_groups(
                request,
                batch_groups,
                first=index == 0,
            )
            for index, batch_groups in enumerate(grouped_batches)
        ),
        requirements,
    )
