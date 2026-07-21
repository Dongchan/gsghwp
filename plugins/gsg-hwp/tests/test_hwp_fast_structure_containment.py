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

from hwp_live_structure_contract import (  # noqa: E402
    FastPageCell,
    FastPageControl,
    FastPageInspection,
    StructurePosition,
)


def test_fast_inspection_names_the_parent_cell_for_nested_pictures() -> None:
    nested = FastPageControl(
        control_type="gso",
        instance_id="picture-1",
        anchor=StructurePosition(list_id=42, paragraph=0, character=0),
    )
    top_level = FastPageControl(
        control_type="gso",
        instance_id="picture-2",
        anchor=StructurePosition(list_id=0, paragraph=8, character=0),
    )
    inspection = FastPageInspection(
        document_id=1,
        full_name=r"C:\report.hwp",
        page=1,
        page_count=1,
        text="",
        controls=(nested, top_level),
        cells=(
            FastPageCell(
                table_instance_id="table-1",
                address="B3",
                list_id=42,
                row_span=1,
                column_span=1,
                text="",
            ),
        ),
    )

    nested_result, top_level_result = inspection.controls

    assert nested_result.parent_table_instance_id == "table-1"
    assert nested_result.parent_cell_address == "B3"
    assert top_level_result.parent_table_instance_id is None
    assert top_level_result.parent_cell_address is None
