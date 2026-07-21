from __future__ import annotations

import pytest

from hwp_live_structure_contract import (
    StructureCell,
    StructureMerge,
    StructurePosition,
    StructureTable,
)
from hwp_live_workflow_table_records import (
    TableRecordMappingError,
    plan_record_table,
)


def _table_with_identity_column(
    *,
    populated: bool,
    category_split: int = 3,
) -> StructureTable:
    cells = [
        StructureCell(
            address="A1",
            row=0,
            column=0,
            owner_address="A1",
            column_span=2,
            text="구분",
        ),
        StructureCell(
            address="B1",
            row=0,
            column=1,
            owner_address="A1",
            text="",
        ),
    ]
    for column, text in enumerate(
        ("조망위치", "이격거리", "표고(m)", "조망방향"),
        start=2,
    ):
        address = f"{'ABCDEF'[column]}1"
        cells.append(
            StructureCell(
                address=address,
                row=0,
                column=column,
                owner_address=address,
                text=text,
            )
        )
    for row in range(1, 5):
        category_owner = "A2" if row <= category_split else f"A{category_split + 2}"
        category_text = (
            "근경"
            if row == 1
            else "원경"
            if row == category_split + 1
            else ""
        )
        cells.append(
            StructureCell(
                address=f"A{row + 1}",
                row=row,
                column=0,
                owner_address=category_owner,
                row_span=(
                    category_split
                    if row == 1
                    else 4 - category_split
                    if row == category_split + 1
                    else 1
                ),
                text=category_text,
            )
        )
        number_address = f"B{row + 1}"
        cells.append(
            StructureCell(
                address=number_address,
                row=row,
                column=1,
                owner_address=number_address,
                text=f"{row:02d}" if populated else "",
            )
        )
        for column in range(2, 6):
            address = f"{'ABCDEF'[column]}{row + 1}"
            cells.append(
                StructureCell(
                    address=address,
                    row=row,
                    column=column,
                    owner_address=address,
                    text="",
                )
            )
    return StructureTable(
        table_ref="table-ref-00000001",
        anchor=StructurePosition(list_id=0, paragraph=0, character=0),
        page_start=9,
        page_end=9,
        rows=5,
        columns=6,
        merges=(
            StructureMerge(
                owner_address="A1",
                row=0,
                column=0,
                row_span=1,
                column_span=2,
            ),
            StructureMerge(
                owner_address="A2",
                row=1,
                column=0,
                row_span=category_split,
                column_span=1,
            ),
            StructureMerge(
                owner_address=f"A{category_split + 2}",
                row=category_split + 1,
                column=0,
                row_span=4 - category_split,
                column_span=1,
            ),
        ),
        cells=tuple(cells),
    )


def _viewpoint_records() -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "조망점번호": f"조망점{index:02d}",
            "이격거리": f"{index}m",
            "표고": f"EL.+{index}.0m",
            "경관거리": "근경" if index <= 2 else "원경",
            "조망방향": "대상지 북동측",
            "조망위치": f"조망 위치 {index}",
            "경도": f"127.{index:06d}",
            "위도": f"37.{index:06d}",
            "X좌표": f"957{index:03d}",
            "Y좌표": f"1958{index:03d}",
            "VP_ID": f"{index:02d}",
        }
        for index in range(1, 5)
    )


def test_maps_headers_when_ambiguous_run_targets_populated_identity_column() -> None:
    # Given
    table = _table_with_identity_column(populated=True)
    records = _viewpoint_records()

    # When
    plan = plan_record_table(table, records)

    # Then
    assert {column.source_key for column in plan.columns} == {
        "이격거리",
        "표고",
        "조망방향",
        "조망위치",
    }


def test_rejects_ambiguous_run_when_target_column_is_blank() -> None:
    # Given
    table = _table_with_identity_column(populated=False)
    records = _viewpoint_records()

    # When / Then
    with pytest.raises(TableRecordMappingError):
        _ = plan_record_table(table, records)


def test_maps_unlabeled_column_when_run_boundaries_match() -> None:
    # Given
    table = _table_with_identity_column(populated=True, category_split=2)
    records = _viewpoint_records()

    # When
    plan = plan_record_table(table, records)

    # Then
    assert "경관거리" in {column.source_key for column in plan.columns}
