from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import NativeDetailedInspection, NativeSnapshot
from hwp_live_native_action_commands import (
    CellCommand,
    MovePositionCommand,
    NativeActionCommand,
    RunCommand,
)
from hwp_live_native_table_topology import table_topology


SelectionRestoreKind = Literal["exact_cursor", "exact_selection", "merged_owner"]


@dataclass(frozen=True, slots=True)
class MergeSelectionExpectation:
    kind: SelectionRestoreKind
    owner_address: str
    table_instance_id: str

    @property
    def observable(self) -> str:
        if self.kind == "merged_owner":
            return f"merged_owner:{self.table_instance_id}:{self.owner_address}"
        return self.kind


def merge_selection_expectation(
    before: NativeSnapshot,
    detail: NativeDetailedInspection,
    table_instance_id: str,
    merged_addresses: tuple[str, ...],
) -> MergeSelectionExpectation:
    owner = merged_addresses[0]
    selection = before.selection
    base_mode = selection.mode & 0x0F
    topology = table_topology(detail, table_instance_id)
    merged = frozenset(merged_addresses)
    if base_mode == 3:
        try:
            selected = frozenset(
                topology.selection_region_by_list_ids(
                    selection.start.list_id,
                    selection.end.list_id,
                )
            )
        except HwpLiveError:
            selected = frozenset(selection.cell_addresses)
        if selected and not selected.isdisjoint(merged):
            # A consumed endpoint cannot be restored exactly after the merge.
            # Match the native C++ merge fallback: collapse the overlapping
            # selection onto the surviving owner and preserve cell-block mode.
            return MergeSelectionExpectation("merged_owner", owner, table_instance_id)
        return MergeSelectionExpectation("exact_selection", owner, table_instance_id)
    if base_mode == 0:
        affected_lists = {
            cell.list_id for cell in topology.cells if cell.address in merged
        }
        if before.cursor.list_id in affected_lists:
            return MergeSelectionExpectation("merged_owner", owner, table_instance_id)
        return MergeSelectionExpectation("exact_cursor", owner, table_instance_id)
    return MergeSelectionExpectation("exact_selection", owner, table_instance_id)


def merge_selection_restore_commands(
    expectation: MergeSelectionExpectation,
    before: NativeSnapshot,
) -> tuple[NativeActionCommand, ...]:
    if expectation.kind == "exact_cursor":
        return (MovePositionCommand(before.cursor),)
    if expectation.kind == "merged_owner":
        commands: tuple[NativeActionCommand, ...] = (
            CellCommand(expectation.owner_address),
        )
        if (before.selection.mode & 0x0F) == 3:
            commands += (RunCommand("TableCellBlock"),)
        return commands
    if (before.selection.mode & 0x0F) != 3:
        return ()
    return (
        MovePositionCommand(before.selection.start),
        RunCommand("TableCellBlock"),
        RunCommand("TableCellBlockExtend"),
        MovePositionCommand(before.selection.end),
    )


def selection_restore_matches(
    expectation: MergeSelectionExpectation,
    before: NativeSnapshot,
    after: NativeSnapshot,
) -> bool:
    if expectation.kind == "merged_owner":
        expected_mode = 3 if (before.selection.mode & 0x0F) == 3 else 0
        return (
            after.control_type == "tbl"
            and after.control_instance_id == expectation.table_instance_id
            and after.cell_address.upper() == expectation.owner_address
            and (after.selection.mode & 0x0F) == expected_mode
        )
    if expectation.kind == "exact_cursor":
        return (after.selection.mode & 0x0F) == 0 and after.cursor == before.cursor
    return (
        after.selection.selected == before.selection.selected
        and after.selection.mode == before.selection.mode
        and after.selection.start == before.selection.start
        and after.selection.end == before.selection.end
    )
