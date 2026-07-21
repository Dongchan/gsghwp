from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from hwp_live_structure_contract import StructureTable
from hwp_visibility_series_contract import (
    CIRCLED_NUMBERS,
    VisibilityRecord,
    VisibilitySeriesPlanError,
)
from hwp_visibility_table_cells import (
    compact_cell_text,
    logical_cells,
    logical_value,
    owner_cells,
    required_cell,
)


_IMAGE_EXTENSIONS: Final = frozenset(
    (".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp")
)
_VIEWPOINT_NUMBER: Final = re.compile(r"(?:(?:예비)?조망점)?([0-9]+)")


def extract_visibility_records(table: StructureTable) -> tuple[VisibilityRecord, ...]:
    owners = owner_cells(table)
    location_header = required_cell(
        owners,
        "조망위치 머리글",
        lambda cell: compact_cell_text(cell.text) == "조망위치",
    )
    distance_header = required_cell(
        owners,
        "이격거리 머리글",
        lambda cell: compact_cell_text(cell.text) == "이격거리",
    )
    elevation_header = required_cell(
        owners,
        "표고 머리글",
        lambda cell: compact_cell_text(cell.text).startswith("표고"),
    )
    if len({location_header.row, distance_header.row, elevation_header.row}) != 1:
        raise VisibilitySeriesPlanError("원본 표의 조망위치·이격거리·표고 머리글 행이 다릅니다")
    number_column = location_header.column - 1
    category_column = number_column - 1
    if category_column < 0:
        raise VisibilitySeriesPlanError("원본 표에서 구분·번호 열을 추론할 수 없습니다")
    logical = logical_cells(table)
    numbered_cells = tuple(
        (cell, int(match.group(1)))
        for cell in owners
        if cell.row > location_header.row
        and cell.column == number_column
        and (
            match := _VIEWPOINT_NUMBER.fullmatch(compact_cell_text(cell.text))
        )
        is not None
    )
    records: list[VisibilityRecord] = []
    seen: set[int] = set()
    for cell, number in sorted(numbered_cells, key=lambda item: item[0].row):
        if number < 1 or number > len(CIRCLED_NUMBERS):
            raise VisibilitySeriesPlanError(
                f"조망점 번호는 1~{len(CIRCLED_NUMBERS)}여야 합니다: {number}"
            )
        if number in seen:
            raise VisibilitySeriesPlanError(f"조망점 {number}번이 중복됩니다")
        seen.add(number)
        records.append(
            VisibilityRecord(
                number=number,
                category=logical_value(logical, cell.row, category_column, "구분"),
                location=logical_value(
                    logical, cell.row, location_header.column, "조망위치"
                ),
                distance=logical_value(
                    logical, cell.row, distance_header.column, "이격거리"
                ),
                elevation=logical_value(
                    logical, cell.row, elevation_header.column, "표고"
                ),
            )
        )
    if not records:
        raise VisibilitySeriesPlanError("원본 표에서 숫자 조망점 레코드를 찾지 못했습니다")
    return tuple(records)


def numbered_image(folder: Path, number: int) -> Path:
    if not folder.is_dir():
        raise VisibilitySeriesPlanError(f"사진 폴더가 없습니다: {folder}")
    matches: list[Path] = []
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.casefold() not in _IMAGE_EXTENSIONS:
            continue
        prefix = re.match(r"^\s*0*([0-9]+)(?=\D|$)", path.stem)
        if prefix is not None and int(prefix.group(1)) == number:
            matches.append(path.resolve())
    if not matches:
        raise VisibilitySeriesPlanError(f"{number}번 사진을 찾지 못했습니다: {folder}")
    if len(matches) > 1:
        raise VisibilitySeriesPlanError(
            f"{number}번 사진이 여러 개입니다: {tuple(path.name for path in matches)}"
        )
    return matches[0]
