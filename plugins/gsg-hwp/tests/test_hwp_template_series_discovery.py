from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_live_template_series_discovery as discovery  # noqa: E402
import hwp_priority_table_series as table_series  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    NativePageCell,
    NativePageControl,
    NativePageInspection,
    NativePosition,
    NativeSnapshot,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_template_repeat import (  # noqa: E402
    TableTemplateRepeatPlan,
    TemplateTableBlock,
    TemplateTextCell,
)
from hwp_operation_contract import (  # noqa: E402
    HwpOperatePostconditions,
    WorkflowResolution,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs  # noqa: E402
from hwp_public_contract import to_public_action_result  # noqa: E402


_LABEL_CELLS = (
    ("A1", "구분"),
    ("B1", "조망위치"),
    ("C1", "이격거리"),
    ("D1", "표고 (m)"),
    ("A3", "가시권 분석"),
    ("B3", "현황사진"),
    ("A5", "분석결과"),
)


def _control(
    instance_id: str,
    *,
    paragraph: int,
    rows: int = 6,
) -> NativePageControl:
    return NativePageControl(
        control_type="tbl",
        instance_id=instance_id,
        anchor=NativePosition(0, paragraph, 0),
        rows=rows,
        columns=4,
    )


def _series_cells(
    table_instance_id: str,
    *,
    missing: frozenset[str] | None = None,
    value: str = "",
) -> tuple[NativePageCell, ...]:
    missing_labels: frozenset[str] = frozenset() if missing is None else missing
    cells = tuple(
        NativePageCell(
            table_instance_id=table_instance_id,
            address=address,
            list_id=index,
            row_span=1,
            column_span=1,
            text=text,
        )
        for index, (address, text) in enumerate(_LABEL_CELLS, start=1)
        if re.sub(r"\s+", "", text) not in missing_labels
    )
    if not value:
        return cells
    return (
        *cells,
        NativePageCell(
            table_instance_id=table_instance_id,
            address="D2",
            list_id=100,
            row_span=1,
            column_span=1,
            text=value,
        ),
    )


def _page(
    page: int,
    controls: tuple[NativePageControl, ...],
    cells: tuple[NativePageCell, ...],
    *,
    page_count: int,
) -> NativePageInspection:
    return NativePageInspection(
        document_id=17,
        full_name="C:/documents/visibility.hwp",
        page=page,
        page_count=page_count,
        text=" ".join(cell.text for cell in cells),
        controls=controls,
        cells=cells,
    )


def _plan(*blocks: TemplateTableBlock) -> TableTemplateRepeatPlan:
    return TableTemplateRepeatPlan(
        source_page=1,
        source_control_id="source-table",
        blocks=blocks,
    )


def _wire_pages(
    monkeypatch: pytest.MonkeyPatch,
    source_page: NativePageInspection,
    detailed_pages: dict[int, NativePageInspection],
) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []
    page_count = max((source_page.page, *detailed_pages))

    def required_snapshot(_window_handle: int) -> NativeSnapshot:
        calls.append(("snapshot", None))
        return cast(
            NativeSnapshot,
            cast(object, SimpleNamespace(page_count=page_count)),
        )

    def required_page(
        _window_handle: int,
        page: int,
        *,
        include_cells: bool,
    ) -> NativePageInspection:
        calls.append(("source_page", (page, include_cells)))
        return source_page

    def read_texts(
        _window_handle: int,
        _snapshot: NativeSnapshot,
        first_page: int,
        last_page: int,
    ) -> dict[int, str]:
        calls.append(("page_texts", (first_page, last_page)))
        return {
            page: detailed_pages[page].text
            for page in range(first_page, last_page + 1)
            if page in detailed_pages
        }

    def inspect_pages(
        _window_handle: int,
        pages: tuple[int, ...],
        *,
        include_cells: bool,
    ) -> tuple[NativePageInspection, ...]:
        calls.append(("detailed_pages", (pages, include_cells)))
        return tuple(detailed_pages[page] for page in pages)

    monkeypatch.setattr(discovery, "required_series_snapshot", required_snapshot)
    monkeypatch.setattr(discovery, "required_series_page", required_page)
    monkeypatch.setattr(discovery, "read_series_page_texts", read_texts)
    monkeypatch.setattr(discovery, "inspect_native_pages", inspect_pages)
    return calls


def test_same_shape_unrelated_table_is_not_added_to_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _page(
        1,
        (_control("source-table", paragraph=0),),
        _series_cells("source-table", value="source value"),
        page_count=2,
    )
    unrelated = _control("unrelated-table", paragraph=1)
    target = _control("series-table-2", paragraph=2)
    unrelated_cells = (
        NativePageCell(
            table_instance_id=unrelated.instance_id,
            address="A1",
            list_id=50,
            row_span=1,
            column_span=1,
            text="일반 현황표",
        ),
    )
    page_two = _page(
        2,
        (unrelated, target),
        (*unrelated_cells, *_series_cells(target.instance_id, value="changed value")),
        page_count=2,
    )
    calls = _wire_pages(monkeypatch, source, {2: page_two})

    found = discovery.discover_table_series(
        101,
        _plan(TemplateTableBlock(), TemplateTableBlock()),
    )

    assert tuple(item.control.instance_id for item in found) == (
        "source-table",
        "series-table-2",
    )
    assert ("source_page", (1, True)) in calls
    assert ("detailed_pages", ((2,), True)) in calls


def test_normal_series_with_changed_values_and_short_last_table_is_discovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _page(
        1,
        (_control("source-table", paragraph=0),),
        _series_cells("source-table", value="source value"),
        page_count=3,
    )
    page_two = _page(
        2,
        (_control("series-table-2", paragraph=0),),
        _series_cells("series-table-2", value="record two"),
        page_count=3,
    )
    page_three = _page(
        3,
        (_control("series-table-3", paragraph=0, rows=5),),
        _series_cells("series-table-3", value="record three"),
        page_count=3,
    )
    _ = _wire_pages(monkeypatch, source, {2: page_two, 3: page_three})

    found = discovery.discover_table_series(
        101,
        _plan(
            TemplateTableBlock(),
            TemplateTableBlock(),
            TemplateTableBlock(delete_rows_from="A6", delete_row_count=1),
        ),
    )

    assert tuple(item.control.instance_id for item in found) == (
        "source-table",
        "series-table-2",
        "series-table-3",
    )


def test_multiple_matching_tables_on_one_page_are_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _page(
        1,
        (_control("source-table", paragraph=0),),
        _series_cells("source-table"),
        page_count=2,
    )
    first = _control("matching-table-a", paragraph=1)
    second = _control("matching-table-b", paragraph=2)
    page_two = _page(
        2,
        (first, second),
        (
            *_series_cells(first.instance_id),
            *_series_cells(second.instance_id),
        ),
        page_count=2,
    )
    _ = _wire_pages(monkeypatch, source, {2: page_two})

    with pytest.raises(discovery.AmbiguousTableSeriesError) as raised:
        _ = discovery.discover_table_series(
            101,
            _plan(TemplateTableBlock(), TemplateTableBlock()),
        )

    assert "matching-table-a" in str(raised.value)
    assert "matching-table-b" in str(raised.value)


def test_partially_damaged_series_signature_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _page(
        1,
        (_control("source-table", paragraph=0),),
        _series_cells("source-table"),
        page_count=2,
    )
    damaged = _control("damaged-series-table", paragraph=1)
    page_two = _page(
        2,
        (damaged,),
        _series_cells(damaged.instance_id, missing=frozenset({"분석결과"})),
        page_count=2,
    )
    calls = _wire_pages(monkeypatch, source, {2: page_two})

    with pytest.raises(discovery.InvalidTableSeriesError, match="일부 라벨"):
        _ = discovery.discover_table_series(
            101,
            _plan(TemplateTableBlock(), TemplateTableBlock()),
        )
    assert ("detailed_pages", ((2,), True)) in calls


def test_partial_page_labels_distributed_across_unrelated_tables_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _page(
        1,
        (_control("source-table", paragraph=0),),
        _series_cells("source-table"),
        page_count=2,
    )
    first = _control("unrelated-a", paragraph=1)
    second = _control("unrelated-b", paragraph=2)
    first_cells = _series_cells(
        first.instance_id,
        missing=frozenset({"표고(m)", "가시권분석", "현황사진", "분석결과"}),
    )
    second_cells = _series_cells(
        second.instance_id,
        missing=frozenset({"구분", "조망위치", "이격거리", "현황사진", "분석결과"}),
    )
    page_two = _page(
        2,
        (first, second),
        (*first_cells, *second_cells),
        page_count=2,
    )
    _ = _wire_pages(monkeypatch, source, {2: page_two})

    found = discovery.discover_table_series(
        101,
        _plan(TemplateTableBlock(), TemplateTableBlock()),
    )

    assert tuple(item.control.instance_id for item in found) == ("source-table",)


def test_priority_recipe_reports_series_ambiguity_as_schema_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def sync(
        _window_handle: int,
        _plan: TableTemplateRepeatPlan,
    ) -> object:
        calls.append("sync")
        raise discovery.AmbiguousTableSeriesError(
            "2쪽에서 원본 표 서명과 일치하는 표가 여러 개입니다"
        )

    def snapshot(_window_handle: int) -> NativeSnapshot:
        return cast(
            NativeSnapshot,
            cast(object, SimpleNamespace(page_count=2)),
        )

    monkeypatch.setattr(table_series, "read_native_snapshot", snapshot)
    monkeypatch.setattr(table_series, "sync_table_template_series", sync)
    plan = _plan(
        TemplateTableBlock(
            text_cells=(
                TemplateTextCell(
                    address="D2",
                    expected_text="template",
                    replacement="record one",
                ),
            )
        ),
        TemplateTableBlock(
            text_cells=(
                TemplateTextCell(
                    address="D2",
                    expected_text="template",
                    replacement="record two",
                ),
            )
        ),
    )
    candidate = cast(
        HwpDocumentCandidate,
        cast(object, SimpleNamespace(window_handle=101)),
    )
    resolution = WorkflowResolution(
        query="가시권 분석표를 동기화",
        status="resolved",
        lookup_microseconds=1,
        workflow_id="table.build_series",
    )

    result = table_series.operate_table_series_recipe(
        candidate,
        resolution,
        HwpPriorityRecipeInputs(
            table_template=plan,
            reconcile_existing=True,
        ),
        HwpOperatePostconditions(),
    )
    public = to_public_action_result(result, ())

    assert calls == ["sync"]
    assert result.status == "schema_conflict"
    assert result.changed is False
    assert public.status == "failed"
    assert public.required_inputs == ()
    assert public.retry_safe is False
