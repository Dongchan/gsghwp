from __future__ import annotations

import re
from dataclasses import dataclass

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import (
    CallCommand,
    IntegerValue,
    NativeActionRequest,
    NativePageControl,
    NativePageInspection,
    NativeSnapshot,
    NativeTextCallResult,
)
from hwp_live_native_batch import (
    execute_native_actions,
    inspect_native_page,
    inspect_native_pages,
    read_native_snapshot,
)
from hwp_live_template_repeat import TableTemplateRepeatPlan


_SERIES_LABELS = (
    "구분",
    "조망위치",
    "이격거리",
    "표고",
    "가시권분석",
    "현황사진",
    "분석결과",
)
_MIN_SERIES_SIGNATURE_LABELS = len(_SERIES_LABELS) - 2
_MAX_CONSECUTIVE_MISSES = 3


class AmbiguousTableSeriesError(HwpLiveError):
    pass


@dataclass(frozen=True, slots=True)
class DiscoveredSeriesTable:
    page: NativePageInspection
    control: NativePageControl


def required_series_snapshot(window_handle: int) -> NativeSnapshot:
    snapshot = read_native_snapshot(window_handle)
    if snapshot is None:
        raise HwpLiveError("표 시리즈 동기화용 현재 문서 상태를 읽지 못했습니다")
    return snapshot


def required_series_page(
    window_handle: int,
    page: int,
    *,
    include_cells: bool,
) -> NativePageInspection:
    inspected = inspect_native_page(window_handle, page, include_cells=include_cells)
    if inspected is None:
        raise HwpLiveError(f"표 시리즈 {page}쪽 구조를 읽지 못했습니다")
    return inspected


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _series_labels(text: str) -> frozenset[str]:
    compact = _compact_text(text)
    return frozenset(label for label in _SERIES_LABELS if label in compact)


def _table_series_labels(
    page: NativePageInspection,
    table_instance_id: str,
) -> frozenset[str]:
    return frozenset(
        label
        for cell in page.cells
        if cell.table_instance_id == table_instance_id
        for label in _series_labels(cell.text)
    )


def _required_series_signature(
    page: NativePageInspection,
    table_instance_id: str,
) -> tuple[str, ...]:
    labels = _table_series_labels(page, table_instance_id)
    missing = tuple(label for label in _SERIES_LABELS if label not in labels)
    if missing:
        labels = "·".join(missing)
        raise HwpLiveError(
            f"동기화 원본 표 셀에서 시리즈 구조 라벨을 찾지 못했습니다: {labels}"
        )
    return tuple(label for label in _SERIES_LABELS if label in labels)


def _matches_series_signature(
    page: NativePageInspection,
    table_instance_id: str,
    signature: tuple[str, ...],
) -> bool:
    return set(signature).issubset(_table_series_labels(page, table_instance_id))


def read_series_page_texts(
    window_handle: int,
    snapshot: NativeSnapshot,
    first_page: int,
    last_page: int,
) -> dict[int, str]:
    pages = tuple(range(first_page, last_page + 1))
    if not pages:
        return {}
    result = execute_native_actions(
        window_handle,
        NativeActionRequest(
            snapshot.document_id,
            snapshot.full_name,
            tuple(
                CallCommand(
                    "GetPageText",
                    (IntegerValue(page - 1), IntegerValue(-1)),
                )
                for page in pages
            ),
        ),
        minimum_version=9,
    )
    if result is None or len(result.call_results) != len(pages):
        raise HwpLiveError("표 시리즈 후보 쪽의 본문을 한 번에 읽지 못했습니다")
    texts: dict[int, str] = {}
    for page, call in zip(pages, result.call_results, strict=True):
        if not isinstance(call, NativeTextCallResult) or call.method != "GetPageText":
            raise HwpLiveError("표 시리즈 후보 쪽 본문 반환 형식이 올바르지 않습니다")
        texts[page] = call.value
    return texts


def discover_table_series(
    window_handle: int,
    plan: TableTemplateRepeatPlan,
) -> tuple[DiscoveredSeriesTable, ...]:
    snapshot = required_series_snapshot(window_handle)
    source_page = required_series_page(
        window_handle,
        plan.source_page,
        include_cells=True,
    )
    sources = tuple(
        control
        for control in source_page.controls
        if control.control_type == "tbl"
        and control.instance_id == plan.source_control_id
    )
    if len(sources) != 1 or sources[0].rows is None or sources[0].columns is None:
        raise HwpLiveError("동기화 원본 표의 행·열 구조를 하나로 찾지 못했습니다")
    source_signature = _required_series_signature(
        source_page,
        sources[0].instance_id,
    )
    source_rows = sources[0].rows
    allowed_rows = {source_rows - block.delete_row_count for block in plan.blocks}
    allowed_rows.add(source_rows)
    last_page = min(
        snapshot.page_count,
        plan.source_page + max(len(plan.blocks) * 3, _MAX_CONSECUTIVE_MISSES),
    )
    page_texts = read_series_page_texts(
        window_handle,
        snapshot,
        plan.source_page + 1,
        last_page,
    )
    found: list[DiscoveredSeriesTable] = [
        DiscoveredSeriesTable(source_page, sources[0])
    ]
    candidate_pages: list[int] = []
    consecutive_misses = 0
    for page_number in range(plan.source_page + 1, last_page + 1):
        page_text = page_texts.get(page_number, "")
        page_labels = _series_labels(page_text)
        if len(page_labels) == len(_SERIES_LABELS):
            candidate_pages.append(page_number)
            consecutive_misses = 0
            if len(candidate_pages) >= len(plan.blocks) - 1:
                break
            continue
        if len(page_labels) >= _MIN_SERIES_SIGNATURE_LABELS:
            raise AmbiguousTableSeriesError(
                f"{page_number}쪽에서 시리즈 라벨 일부만 확인되어 기존 표 여부를 "
                + "확정할 수 없습니다"
            )
        consecutive_misses += 1
        if consecutive_misses >= _MAX_CONSECUTIVE_MISSES:
            break
    detailed_pages = inspect_native_pages(
        window_handle,
        tuple(candidate_pages),
        include_cells=True,
    )
    for detailed in detailed_pages:
        shaped_controls = tuple(
            control
            for control in detailed.controls
            if control.control_type == "tbl"
            and control.rows in allowed_rows
            and control.columns == sources[0].columns
        )
        controls = tuple(
            control
            for control in shaped_controls
            if _matches_series_signature(
                detailed,
                control.instance_id,
                source_signature,
            )
        )
        partial_controls = tuple(
            control
            for control in shaped_controls
            if control not in controls
            and len(_table_series_labels(detailed, control.instance_id))
            >= _MIN_SERIES_SIGNATURE_LABELS
        )
        if len(controls) > 1 or partial_controls:
            ambiguous_controls = (*controls, *partial_controls)
            control_ids = ", ".join(
                control.instance_id
                for control in sorted(
                    ambiguous_controls,
                    key=lambda item: (
                        item.anchor.paragraph,
                        item.anchor.character,
                    ),
                )
            )
            raise AmbiguousTableSeriesError(
                f"{detailed.page}쪽에서 시리즈 표 후보를 하나로 확정할 수 없습니다: "
                + control_ids
            )
        if not controls:
            raise AmbiguousTableSeriesError(
                f"{detailed.page}쪽 본문에서 시리즈 라벨을 찾았지만 어느 표에 "
                + "속하는지 확정할 수 없습니다"
            )
        found.extend(
            DiscoveredSeriesTable(detailed, control)
            for control in sorted(
                controls,
                key=lambda item: (
                    item.anchor.paragraph,
                    item.anchor.character,
                ),
            )
        )
        if len(found) >= len(plan.blocks):
            found = found[: len(plan.blocks)]
            break
    if not found or found[0].control.instance_id != plan.source_control_id:
        raise HwpLiveError("동기화 원본 표가 시리즈의 첫 표가 아닙니다")
    return tuple(found)
