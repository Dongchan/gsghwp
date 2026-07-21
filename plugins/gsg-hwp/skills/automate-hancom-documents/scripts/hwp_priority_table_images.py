from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import (
    CaptureTableCommand,
    CellCommand,
    InsertPictureCommand,
    NativeActionCommand,
    SelectControlCommand,
)
from hwp_live_structure_contract import StructureTable
from hwp_operation_contract import HwpOperateAssets, HwpOperatePolicy


def prepare_table_image_commands(
    table: StructureTable,
    assets: HwpOperateAssets,
    policy: HwpOperatePolicy,
) -> tuple[tuple[NativeActionCommand, ...], tuple[str, ...]]:
    control_id = table.control_instance_id
    if control_id is None:
        raise HwpLiveError("그림 대상 표의 네이티브 개체 ID가 없습니다")
    cells = {cell.address: cell for cell in table.cells}
    commands: list[NativeActionCommand] = [
        SelectControlCommand(control_id),
        CaptureTableCommand(),
    ]
    inserted: list[str] = []
    for requested, raw_path in assets.images.items():
        address = requested.strip().upper()
        cell = cells.get(address)
        if cell is None:
            raise HwpLiveError(f"그림 대상 셀이 없습니다: {address}")
        if policy.preserve_existing_images and cell.has_picture:
            continue
        commands.extend((CellCommand(address), InsertPictureCommand(raw_path)))
        inserted.append(address)
    return tuple(commands), tuple(inserted)
