from __future__ import annotations

import re
from pathlib import Path
from unicodedata import normalize

from pydantic import Field, field_validator

from hwp_errors import HwpLiveError
from hwp_live_structure_contract import (
    StructureCell,
    StructureTable,
    TableCellUpdate,
)
from hwp_live_values import ContractModel


_IMAGE_EXTENSIONS = frozenset(
    {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
)


def _address(value: str) -> str:
    result = value.strip().upper()
    if re.fullmatch(r"[A-Z]+[1-9][0-9]*", result) is None:
        raise ValueError("cell address must look like B3")
    return result


class CellTransfer(ContractModel):
    source_address: str = Field(max_length=20)
    target_address: str = Field(max_length=20)

    @field_validator("source_address", "target_address")
    @classmethod
    def normalize_address(cls, value: str) -> str:
        return _address(value)


def _cell(table: StructureTable, address: str, role: str) -> StructureCell:
    cell = next((candidate for candidate in table.cells if candidate.address == address), None)
    if cell is None:
        raise HwpLiveError(f"{role} 표에 {address} 셀이 없습니다")
    if cell.owner_address != cell.address:
        raise HwpLiveError(f"{role} {address} 셀은 병합된 {cell.owner_address} 셀입니다")
    return cell


def transfer_updates(
    source: StructureTable,
    target: StructureTable,
    transfers: tuple[CellTransfer, ...],
) -> tuple[TableCellUpdate, ...]:
    if not transfers:
        raise HwpLiveError("반복 표로 옮길 셀 대응값이 없습니다")
    target_addresses = tuple(item.target_address for item in transfers)
    if len(target_addresses) != len(set(target_addresses)):
        raise HwpLiveError("한 반복 표의 같은 셀로 두 값을 옮길 수 없습니다")
    updates: list[TableCellUpdate] = []
    for transfer in transfers:
        source_cell = _cell(source, transfer.source_address, "원본")
        target_cell = _cell(target, transfer.target_address, "대상")
        if target_cell.has_picture or target_cell.has_nested_table:
            raise HwpLiveError(f"대상 {target_cell.address} 셀에 개체가 있습니다")
        updates.append(
            TableCellUpdate(
                address=target_cell.address,
                expected_text=target_cell.text,
                replacement=source_cell.text,
            )
        )
    return tuple(updates)


def _compact(value: str) -> str:
    return "".join(
        character.casefold()
        for character in normalize("NFKC", value)
        if character.isalnum()
    )


def image_for_keys(
    folder: Path,
    match_keys: tuple[str, ...],
    role_keys: tuple[str, ...] = (),
) -> Path:
    root = folder.expanduser().resolve()
    if not root.is_dir():
        raise HwpLiveError(f"사진 폴더가 없습니다: {root}")
    keys = tuple(filter(None, (_compact(value) for value in match_keys)))
    roles = tuple(filter(None, (_compact(value) for value in role_keys)))
    if not keys:
        raise HwpLiveError("사진 파일명과 대응할 조망점 식별값이 없습니다")
    candidates = [
        path
        for path in sorted(root.iterdir(), key=lambda item: item.name.casefold())
        if path.is_file()
        and path.suffix.casefold() in _IMAGE_EXTENSIONS
        and all(key in _compact(path.stem) for key in keys)
        and all(role in _compact(path.stem) for role in roles)
    ]
    if not candidates:
        label = ", ".join((*match_keys, *role_keys))
        raise HwpLiveError(f"사진 폴더에서 '{label}' 파일을 찾지 못했습니다")
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise HwpLiveError(f"조건에 맞는 사진이 여러 개입니다: {names}")
    return candidates[0]


class TablePropagationTarget(ContractModel):
    page: int = Field(ge=1)
    table_ref: str = Field(min_length=16, max_length=80)
    transfers: tuple[CellTransfer, ...] = Field(min_length=1, max_length=1_000)


class TablePropagationPlan(ContractModel):
    source_page: int = Field(ge=1)
    source_table_ref: str = Field(min_length=16, max_length=80)
    targets: tuple[TablePropagationTarget, ...] = Field(min_length=1, max_length=1_000)


class PropagatedTableResult(ContractModel):
    page: int = Field(ge=1)
    table_ref: str = Field(min_length=16, max_length=80)
    updated_addresses: tuple[str, ...]


class TablePropagationResult(ContractModel):
    source_table_ref: str
    target_results: tuple[PropagatedTableResult, ...]
    transferred_cell_count: int = Field(ge=1)
    native_elapsed_microseconds: int = Field(ge=0)


class FolderImageCell(ContractModel):
    address: str = Field(max_length=20)
    expected_text: str = Field(max_length=200_000)
    match_keys: tuple[str, ...] = Field(min_length=1, max_length=20)
    role_keys: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator("address")
    @classmethod
    def normalize_address(cls, value: str) -> str:
        return _address(value)


class FolderImageTarget(ContractModel):
    page: int = Field(ge=1)
    table_ref: str = Field(min_length=16, max_length=80)
    cells: tuple[FolderImageCell, ...] = Field(min_length=1, max_length=100)


class FolderImagePlan(ContractModel):
    folder: Path
    targets: tuple[FolderImageTarget, ...] = Field(min_length=1, max_length=1_000)


class InsertedImageTableResult(ContractModel):
    page: int = Field(ge=1)
    table_ref: str = Field(min_length=16, max_length=80)
    inserted_addresses: tuple[str, ...]
    inserted_paths: tuple[Path, ...]


class FolderImageResult(ContractModel):
    target_results: tuple[InsertedImageTableResult, ...]
    inserted_image_count: int = Field(ge=1)
    native_elapsed_microseconds: int = Field(ge=0)
