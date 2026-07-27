from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_contract import LayoutPlan, PageBreakBlock  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    IntegerValue,
    ParameterActionCommand,
)
from hwp_live_native_layout import NativeLayoutContext, build_native_layout_request  # noqa: E402
from hwp_live_operation_recipe import reference_append_page_count  # noqa: E402
from hwp_reference_layout_contract import (  # noqa: E402
    ReferenceLayoutBlock,
    ReferenceMerge,
    ReferenceStyle,
    StyleRegion,
    TextAnchor,
    VisibleEdge,
)
from hwp_reference_layout_geometry import SectionPageGeometry  # noqa: E402


REFERENCE_LAYOUT_FIXTURES = (
    "reference_layout_vision_goals.json",
    "reference_layout_namwon_strategy.json",
    "reference_layout_three_theme_flow.json",
    "reference_layout_carbon_org_chart.json",
    "reference_layout_building_overview.json",
)


def _benchmark_layout(rows: int = 24, columns: int = 5) -> ReferenceLayoutBlock:
    return ReferenceLayoutBlock(
        kind="reference_layout",
        row_breakpoints=tuple(index / rows for index in range(rows + 1)),
        column_breakpoints=(0.0, 24 / 170, 58 / 170, 95 / 170, 132 / 170, 1.0),
        merges=(
            ReferenceMerge(row=0, column=0, row_span=1, column_span=columns),
            ReferenceMerge(row=1, column=0, row_span=2, column_span=1),
        ),
        visible_edges=tuple(
            VisibleEdge(
                orientation="horizontal",
                line=index,
                start=1 if index == 2 else 0,
                end=columns,
            )
            for index in range(rows + 1)
        )
        + tuple(
            VisibleEdge(
                orientation="vertical",
                line=index,
                start=1 if 0 < index < columns else 0,
                end=rows,
            )
            for index in range(columns + 1)
        ),
        styles=(
            ReferenceStyle(key="body", font_name="맑은 고딕", font_size_pt=8),
            ReferenceStyle(
                key="header",
                font_name="맑은 고딕",
                font_size_pt=11,
                bold=True,
                fill_color=(250, 225, 216),
            ),
        ),
        style_regions=(
            StyleRegion(top=0, left=0, bottom=rows, right=columns, style_key="body"),
            StyleRegion(top=0, left=0, bottom=1, right=columns, style_key="header"),
        ),
        text_anchors=(
            TextAnchor(row=0, column=0, text="건 축 개 요", style_key="header"),
            TextAnchor(row=2, column=1, text="검증 항목", style_key="body"),
        ),
    )


def _page() -> SectionPageGeometry:
    return SectionPageGeometry.from_mm(
        paper_width_mm=210,
        paper_height_mm=297,
        landscape=False,
        left_margin_mm=20,
        right_margin_mm=20,
        top_margin_mm=15,
        bottom_margin_mm=10.5,
        header_mm=0,
        footer_mm=0,
        gutter_mm=0,
        gutter_type=0,
    )


def test_reference_layout_compiles_to_one_high_level_native_command() -> None:
    request = build_native_layout_request(
        NativeLayoutContext(
            document_id=1,
            full_name=r"D:\samples\reference.hwp",
            style_ids=(),
            page_geometry=_page(),
        ),
        LayoutPlan(blocks=(_benchmark_layout(),)),
        {},
    )

    bulk = [command for command in request.commands if isinstance(command, ParameterActionCommand)]

    assert len(request.commands) == 1
    assert len(bulk) == 1
    assert bulk[0].action == "ReferenceLayoutBulk"
    assert {array.name for array in bulk[0].arrays} >= {
        "ColumnWidths",
        "RowHeights",
        "MergeRows",
        "EdgeLines",
        "RegionTop",
        "TextValues",
    }
    assert all(array.count > 0 for array in bulk[0].arrays)
    array_sizes = {array.name: array.count for array in bulk[0].arrays}
    assert all(
        0 <= item.index < array_sizes[item.name]
        for item in bulk[0].array_values
    )
    assert any(item.index == 0 for item in bulk[0].array_values)


def test_reference_layout_after_last_page_does_not_delete_a_guessed_trailing_page() -> None:
    layout = _benchmark_layout()
    request = build_native_layout_request(
        NativeLayoutContext(
            document_id=1,
            full_name="test.hwp",
            style_ids=(),
            page_count=28,
            page_geometry=_page(),
            page_number=29,
        ),
        LayoutPlan(target="after_page", page=28, blocks=(layout,)),
        {},
    )

    assert all(
        not (
            isinstance(command, ParameterActionCommand)
            and command.action == "DeletePage"
        )
        for command in request.commands
    )
    last = request.commands[-1]
    assert isinstance(last, ParameterActionCommand)
    assert last.action == "ReferenceLayoutBulk"
    assert sum(
        isinstance(command, ParameterActionCommand)
        and command.action == "ReferenceLayoutBulk"
        for command in request.commands
    ) == 1


def test_reference_layout_pages_use_each_facing_page_gutter() -> None:
    page = SectionPageGeometry.from_mm(
        paper_width_mm=210,
        paper_height_mm=297,
        landscape=False,
        left_margin_mm=20,
        right_margin_mm=20,
        top_margin_mm=15,
        bottom_margin_mm=15,
        header_mm=10,
        footer_mm=10,
        gutter_mm=6,
        gutter_type=1,
    )
    layout = _benchmark_layout()
    request = build_native_layout_request(
        NativeLayoutContext(
            document_id=1,
            full_name="test.hwp",
            style_ids=(),
            page_geometry=page,
            page_number=1,
        ),
        LayoutPlan(
            blocks=(
                layout,
                PageBreakBlock(kind="page_break"),
                layout,
            )
        ),
        {},
    )
    bulk = [
        command
        for command in request.commands
        if isinstance(command, ParameterActionCommand)
        and command.action == "ReferenceLayoutBulk"
    ]
    lefts: list[int] = []
    for command in bulk:
        value = next(
            setter.value
            for setter in command.setters
            if setter.path == "BodyLeft"
        )
        assert isinstance(value, IntegerValue)
        lefts.append(value.value)

    odd = page.usable_area(page_number=1)
    even = page.usable_area(page_number=2)
    assert lefts[0] - lefts[1] == odd.left - even.left


def test_reference_layout_batch_tracks_only_one_trailing_blank_page() -> None:
    layout = _benchmark_layout()
    plan = LayoutPlan(
        target="after_page",
        page=28,
        blocks=(
            layout,
            PageBreakBlock(kind="page_break"),
            layout,
        ),
    )

    assert reference_append_page_count(plan, 28) == 2
    assert reference_append_page_count(plan, 27) == 0


def test_bulk_payload_scales_with_components_not_physical_cells() -> None:
    layout = _benchmark_layout()
    request = build_native_layout_request(
        NativeLayoutContext(
            document_id=1,
            full_name="test.hwp",
            style_ids=(),
            page_geometry=_page(),
        ),
        LayoutPlan(blocks=(layout,)),
        {},
    )
    command = request.commands[0]
    assert isinstance(command, ParameterActionCommand)

    physical_cells = 24 * 5
    component_count = (
        len(layout.row_breakpoints)
        + len(layout.column_breakpoints)
        + len(layout.merges)
        + len(layout.visible_edges)
        + len(layout.style_regions)
        + len(layout.text_anchors)
    )

    assert len(command.array_values) < physical_cells * 4
    assert len(command.array_values) < component_count * 8


def test_building_overview_fixture_stays_component_compressed() -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "reference_layout_building_overview.json"
    )
    layout = ReferenceLayoutBlock.model_validate(
        json.loads(fixture.read_text(encoding="utf-8"))
    )

    request = build_native_layout_request(
        NativeLayoutContext(
            document_id=1,
            full_name="test.hwp",
            style_ids=(),
            page_geometry=_page(),
        ),
        LayoutPlan(blocks=(layout,)),
        {},
    )

    assert len(request.commands) == 1
    assert len(layout.row_breakpoints) - 1 == 31
    assert len(layout.column_breakpoints) - 1 == 6
    assert len(layout.merges) == 47
    assert len(layout.text_anchors) == 89
    assert "rows" not in layout.model_dump()


def test_all_reference_layout_fixtures_compile_to_one_bulk_command() -> None:
    fixture_root = Path(__file__).parent / "fixtures"
    for fixture_name in REFERENCE_LAYOUT_FIXTURES:
        layout = ReferenceLayoutBlock.model_validate(
            json.loads((fixture_root / fixture_name).read_text(encoding="utf-8"))
        )
        request = build_native_layout_request(
            NativeLayoutContext(
                document_id=1,
                full_name="test.hwp",
                style_ids=(),
                page_geometry=_page(),
            ),
            LayoutPlan(blocks=(layout,)),
            {},
        )
        command = request.commands[0]
        rows = len(layout.row_breakpoints) - 1
        columns = len(layout.column_breakpoints) - 1
        component_count = (
            len(layout.row_breakpoints)
            + len(layout.column_breakpoints)
            + len(layout.merges)
            + len(layout.visible_edges)
            + len(layout.style_regions)
            + len(layout.text_anchors)
        )

        assert len(request.commands) == 1
        assert isinstance(command, ParameterActionCommand)
        assert command.action == "ReferenceLayoutBulk"
        assert component_count < rows * columns * 4
        assert "rows" not in layout.model_dump()
