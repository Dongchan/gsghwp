from __future__ import annotations

import sys
from dataclasses import dataclass, field
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

import hwp_live_template_repeat as repeat_runtime  # noqa: E402
import hwp_priority_table_series as table_series  # noqa: E402
from hwp_live_contract import OpenDocument  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    NativeActionRequest,
    NativeActionResult,
    NativeCharacterFormat,
    NativeDetailedCaption,
    NativeDetailedInspection,
    NativePageCell,
    NativePageControl,
    NativePageInspection,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
    PasteTableCommand,
    RunCommand,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_template_repeat import (  # noqa: E402
    TableTemplateRepeatPlan,
    TableTemplateRepeatResult,
    TemplateImageCell,
    TemplateTableBlock,
    TemplateTextCell,
    repeat_table_template,
)
from hwp_operation_contract import (  # noqa: E402
    HwpOperateInputs,
    HwpOperatePostconditions,
    OperationResult,
    WorkflowResolution,
)
from hwp_operation_idempotency import (  # noqa: E402
    OperationIdempotency,
    OperationTicket,
)
from hwp_operation_journal import OperationJournal  # noqa: E402
from hwp_operation_verification import enforce_operation_verification  # noqa: E402
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs  # noqa: E402
from hwp_public_contract import to_public_action_result  # noqa: E402


POSITION = NativePosition(0, 0, 0)
CHARACTER_FORMAT = NativeCharacterFormat("함초롬바탕", 1_000, False, 0)
PARAGRAPH_FORMAT = NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0)


@dataclass(slots=True)
class _RepeatState:
    inspection_calls: list[tuple[int, tuple[int, ...], bool]] = field(
        default_factory=list
    )
    detailed_inspection_calls: list[tuple[int, int]] = field(default_factory=list)
    requests: list[NativeActionRequest] = field(default_factory=list)
    snapshots: list[NativeSnapshot] = field(default_factory=list)
    events: list[str] = field(default_factory=list)


def _snapshot(
    *,
    current_page: int,
    page_count: int,
    modified: bool,
    selection: NativeSelection | None = None,
    selected_text: str = "",
    control_type: str = "",
    control_instance_id: str = "",
    cell_address: str = "",
) -> NativeSnapshot:
    return NativeSnapshot(
        document_id=17,
        full_name="C:/documents/repeat.hwp",
        current_page=current_page,
        page_count=page_count,
        modified=modified,
        cursor=POSITION,
        selection=selection or NativeSelection(False, POSITION, POSITION),
        selected_text=selected_text,
        control_type=control_type,
        control_instance_id=control_instance_id,
        cell_address=cell_address,
        style_id=0,
        character_format=CHARACTER_FORMAT,
        paragraph_format=PARAGRAPH_FORMAT,
    )


def _page(
    page: int,
    control_id: str,
    *,
    rows: int = 1,
    cell_texts: dict[str, str] | None = None,
    page_text: str = "",
    picture_addresses: tuple[str, ...] = (),
    picture_sizes_hwpunit: dict[str, tuple[int, int]] | None = None,
) -> NativePageInspection:
    cell_texts = {"A1": ""} if cell_texts is None else cell_texts
    picture_sizes_hwpunit = (
        {} if picture_sizes_hwpunit is None else picture_sizes_hwpunit
    )
    table_anchor = NativePosition(page * 10, 0, 0)
    cells = tuple(
        NativePageCell(
            control_id,
            address,
            page * 100 + index,
            1,
            1,
            text,
        )
        for index, (address, text) in enumerate(cell_texts.items(), start=1)
    )
    cell_by_address = {cell.address: cell for cell in cells}
    pictures = tuple(
        NativePageControl(
            "gso",
            f"{control_id}-{address}-picture",
            NativePosition(cell_by_address[address].list_id, 0, 0),
            None,
            None,
            *picture_sizes_hwpunit.get(address, (None, None)),
        )
        for address in picture_addresses
    )
    return NativePageInspection(
        document_id=17,
        full_name="C:/documents/repeat.hwp",
        page=page,
        page_count=2,
        text=page_text,
        controls=(
            NativePageControl("tbl", control_id, table_anchor, rows, 2),
            *pictures,
        ),
        cells=cells,
    )


def _hwpunit(millimeters: float) -> int:
    return round(millimeters * 7_200 / 25.4)


def _plan() -> TableTemplateRepeatPlan:
    return TableTemplateRepeatPlan(
        source_page=1,
        source_control_id="source-table",
        blocks=(TemplateTableBlock(), TemplateTableBlock()),
    )


def _verification_plan() -> TableTemplateRepeatPlan:
    return TableTemplateRepeatPlan(
        source_page=1,
        source_control_id="source-table",
        caption_title="Project-01",
        blocks=(
            TemplateTableBlock(
                text_cells=(
                    TemplateTextCell(
                        address="A1",
                        expected_text="template",
                        replacement="first record",
                    ),
                ),
                images=(
                    TemplateImageCell(
                        address="B1",
                        expected_text="picture placeholder",
                        path=Path("C:/fixtures/chart.png"),
                        width_mm=30,
                        height_mm=20,
                    ),
                ),
                delete_rows_from="A2",
                delete_row_count=1,
            ),
            TemplateTableBlock(
                text_cells=(
                    TemplateTextCell(
                        address="A1",
                        expected_text="template",
                        replacement="second record",
                    ),
                ),
                images=(
                    TemplateImageCell(
                        address="B1",
                        expected_text="picture placeholder",
                        path=Path("C:/fixtures/chart.png"),
                        width_mm=30,
                        height_mm=20,
                    ),
                ),
                delete_rows_from="A2",
                delete_row_count=1,
            ),
        ),
    )


def _install_repeat_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    plan: TableTemplateRepeatPlan,
    defect: str | None = None,
) -> _RepeatState:
    source_cell_texts = {
        cell.address: cell.expected_text
        for cell in (*plan.blocks[0].text_cells, *plan.blocks[0].images)
    }
    if not source_cell_texts:
        source_cell_texts = {"A1": ""}
    source_rows = 2 if plan.blocks[0].delete_row_count else 1
    before_page = _page(
        1,
        "source-table",
        rows=source_rows,
        cell_texts=source_cell_texts,
        page_text=(
            f"표 3 {plan.caption_title}" if plan.caption_title is not None else ""
        ),
    )
    before = _snapshot(current_page=1, page_count=1, modified=False)
    after_selection_position = NativePosition(201, 0, 0)
    after = _snapshot(
        current_page=2,
        page_count=2,
        modified=True,
        selection=NativeSelection(
            True,
            after_selection_position,
            after_selection_position,
        ),
        control_type="tbl",
        control_instance_id="created-table",
        cell_address="A1",
    )
    snapshots = [before]
    if plan.caption_title is not None:
        caption_start = NativePosition(99, 0, 0)
        caption_end = NativePosition(
            99,
            0,
            len(plan.caption_title) + 3,
        )
        snapshots.append(
            _snapshot(
                current_page=1,
                page_count=1,
                modified=False,
                selection=NativeSelection(True, caption_start, caption_end),
                selected_text=plan.caption_title,
                control_type="tbl",
                control_instance_id="source-table",
            )
        )
    snapshots.append(after)
    snapshot_values = iter(snapshots)
    state = _RepeatState()

    def execute(
        _window_handle: int,
        request: NativeActionRequest,
    ) -> NativeActionResult:
        state.requests.append(request)
        state.events.append("action")
        created = (
            ("created-table",)
            if any(
                isinstance(command, PasteTableCommand) for command in request.commands
            )
            else ()
        )
        return NativeActionResult(
            commands_executed=len(request.commands),
            actions_executed=sum(
                isinstance(command, RunCommand) for command in request.commands
            ),
            text_insertions=0,
            image_insertions=0,
            elapsed_microseconds=len(request.commands),
            created_control_ids=created,
        )

    post_pages: list[NativePageInspection] = []
    detailed_by_page: dict[int, NativeDetailedInspection] = {}
    for index, (table_id, block) in enumerate(
        zip(("source-table", "created-table"), plan.blocks, strict=True),
    ):
        page = index + 1
        expected_rows = source_rows - block.delete_row_count
        rows = (
            source_rows
            if defect == "wrong_rows" and table_id == "created-table"
            else expected_rows
        )
        cell_texts = {
            cell.address: (
                "wrong text"
                if defect == "wrong_text" and table_id == "created-table"
                else cell.replacement
            )
            for cell in block.text_cells
        }
        cell_texts.update(
            {image.address: image.expected_text for image in block.images}
        )
        if not cell_texts:
            cell_texts = {"A1": ""}
        pictures = tuple(
            image.address
            for image in block.images
            if not (defect == "wrong_picture" and table_id == "created-table")
        )
        picture_sizes_hwpunit = {
            image.address: (
                (1, 1)
                if defect == "wrong_picture_size" and table_id == "created-table"
                else (_hwpunit(image.width_mm), _hwpunit(image.height_mm))
            )
            for image in block.images
        }
        caption = ""
        detailed_captions: tuple[NativeDetailedCaption, ...] = ()
        if plan.caption_title is not None:
            caption_title = (
                "wrong caption"
                if defect == "wrong_caption" and table_id == "created-table"
                else f"Project-{index + 1:02d}"
            )
            caption = f"표 {index + 3} {caption_title}"
            caption_table_id = (
                "other-table"
                if defect == "caption_on_other_table" and table_id == "created-table"
                else table_id
            )
            detailed_captions = (
                NativeDetailedCaption(
                    caption_table_id,
                    caption,
                    True,
                    None,
                    None,
                    page,
                    page,
                ),
            )
        post_pages.append(
            _page(
                page,
                table_id,
                rows=rows,
                cell_texts=cell_texts,
                page_text=caption,
                picture_addresses=pictures,
                picture_sizes_hwpunit=picture_sizes_hwpunit,
            )
        )
        detailed_by_page[page] = NativeDetailedInspection(
            document_id=17,
            full_name="C:/documents/repeat.hwp",
            page=page,
            page_count=2,
            text=caption,
            controls=(),
            cells=(),
            captions=detailed_captions,
        )
    if defect == "missing_table":
        post_pages = post_pages[:1]

    def inspect(
        window_handle: int,
        pages: tuple[int, ...],
        *,
        include_cells: bool = True,
    ) -> tuple[NativePageInspection, ...]:
        state.inspection_calls.append((window_handle, pages, include_cells))
        state.events.append("inspect")
        return tuple(post_pages)

    def required_page(_window_handle: int, _page_number: int) -> NativePageInspection:
        return before_page

    def inspect_detail(
        window_handle: int,
        page: int,
    ) -> NativeDetailedInspection:
        state.detailed_inspection_calls.append((window_handle, page))
        return detailed_by_page[page]

    def fit(
        _path: Path,
        *,
        width_mm: float,
        height_mm: float,
    ) -> tuple[float, float]:
        return width_mm, height_mm

    def snapshot(_window_handle: int) -> NativeSnapshot:
        value = next(snapshot_values)
        state.snapshots.append(value)
        state.events.append("snapshot")
        return value

    monkeypatch.setattr(repeat_runtime, "_required_page", required_page)
    monkeypatch.setattr(repeat_runtime, "_required_action", execute)
    monkeypatch.setattr(repeat_runtime, "read_native_snapshot", snapshot)
    monkeypatch.setattr(repeat_runtime, "inspect_native_pages", inspect)
    monkeypatch.setattr(
        repeat_runtime,
        "inspect_native_structure",
        inspect_detail,
        raising=False,
    )
    monkeypatch.setattr(repeat_runtime, "fit_image_in_box", fit, raising=False)
    return state


def test_repeat_uses_product_readback_after_snapshot_for_a_rich_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _verification_plan()
    state = _install_repeat_runtime(
        monkeypatch,
        plan=plan,
    )

    result = repeat_table_template(101, plan)

    assert len(state.requests) >= 1
    assert all(request.commands for request in state.requests)
    assert state.inspection_calls == [(101, (1, 2), True)]
    assert state.events[-2:] == ["snapshot", "inspect"]
    assert state.snapshots[-1].selection.selected is True
    assert state.snapshots[-1].control_instance_id == "created-table"
    assert result.verified is True
    assert result.verification_error is None
    assert result.text_cell_count == 2
    assert result.image_count == 2


def test_repeat_readback_rejects_a_missing_created_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _verification_plan()
    state = _install_repeat_runtime(
        monkeypatch,
        plan=plan,
        defect="missing_table",
    )

    result = repeat_table_template(101, plan)

    assert state.inspection_calls == [(101, (1, 2), True)]
    assert result.verified is False
    assert result.verification_error is not None
    assert "created-table" in result.verification_error


@pytest.mark.parametrize(
    "defect",
    ("wrong_rows", "wrong_text", "wrong_picture", "wrong_caption"),
)
def test_repeat_readback_rejects_each_mismatched_postcondition(
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
) -> None:
    plan = _verification_plan()
    state = _install_repeat_runtime(
        monkeypatch,
        plan=plan,
        defect=defect,
    )

    result = repeat_table_template(101, plan)

    assert state.inspection_calls == [(101, (1, 2), True)]
    assert result.verified is False
    assert result.verification_error


def test_repeat_readback_rejects_one_wrong_sized_gso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _verification_plan()
    _ = _install_repeat_runtime(
        monkeypatch,
        plan=plan,
        defect="wrong_picture_size",
    )

    result = repeat_table_template(101, plan)

    assert result.verified is False
    assert result.verification_error is not None
    assert "그림" in result.verification_error
    assert "크기" in result.verification_error


def test_repeat_caption_rejects_matching_text_attached_only_to_another_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _verification_plan()
    state = _install_repeat_runtime(
        monkeypatch,
        plan=plan,
        defect="caption_on_other_table",
    )

    result = repeat_table_template(101, plan)

    assert result.verified is False
    assert result.verification_error is not None
    assert "created-table" in result.verification_error
    assert state.detailed_inspection_calls


def _repeat_result(
    *,
    verified: bool,
    verification_error: str | None,
) -> TableTemplateRepeatResult:
    return TableTemplateRepeatResult(
        source_control_id="source-table",
        created_control_ids=("created-table",),
        table_count=2,
        text_cell_count=0,
        image_count=0,
        commands_executed=7,
        native_elapsed_microseconds=70,
        current_page=2,
        page_count=2,
        modified=True,
        verified=verified,
        verification_error=verification_error,
    )


def _operate(
    monkeypatch: pytest.MonkeyPatch,
    repeated: TableTemplateRepeatResult,
    *,
    reconcile_existing: bool = False,
    route_calls: list[str] | None = None,
) -> OperationResult:
    calls = [] if route_calls is None else route_calls

    def snapshot(_window_handle: int) -> NativeSnapshot:
        return _snapshot(
            current_page=1,
            page_count=1,
            modified=False,
        )

    def repeat(
        _window_handle: int,
        _repeat_plan: TableTemplateRepeatPlan,
    ) -> TableTemplateRepeatResult:
        calls.append("repeat")
        return repeated

    def sync(
        _window_handle: int,
        _repeat_plan: TableTemplateRepeatPlan,
    ) -> TableTemplateRepeatResult:
        calls.append("sync")
        return repeated

    monkeypatch.setattr(table_series, "read_native_snapshot", snapshot)
    monkeypatch.setattr(table_series, "repeat_table_template", repeat)
    monkeypatch.setattr(table_series, "sync_table_template_series", sync)
    resolution = WorkflowResolution(
        query="표를 두 번 반복",
        status="resolved",
        lookup_microseconds=1,
        workflow_id="table.repeat_template",
    )
    candidate = cast(
        HwpDocumentCandidate,
        cast(object, SimpleNamespace(window_handle=101)),
    )
    return table_series.operate_table_series_recipe(
        candidate,
        resolution,
        HwpPriorityRecipeInputs(
            table_template=_plan(),
            reconcile_existing=reconcile_existing,
        ),
        HwpOperatePostconditions(),
    )


def test_table_repeat_reports_verified_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _operate(
        monkeypatch,
        _repeat_result(verified=True, verification_error=None),
    )
    enforced = enforce_operation_verification("table.repeat_template", result)
    public = to_public_action_result(enforced, ())

    assert enforced.status == "executed"
    assert enforced.changed is True
    assert enforced.verified is True
    assert enforced.commands_executed == 7
    assert enforced.commands_completed == 7
    assert enforced.retry_safe is True
    assert public.status == "succeeded"
    assert public.verified is True


def test_table_repeat_does_not_hide_a_failed_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _operate(
        monkeypatch,
        _repeat_result(
            verified=False,
            verification_error="created-table 표를 사후 조회에서 찾지 못했습니다",
        ),
    )
    enforced = enforce_operation_verification("table.repeat_template", result)
    public = to_public_action_result(enforced, ())

    assert enforced.status == "partial_change"
    assert enforced.changed is True
    assert enforced.verified is False
    assert enforced.commands_executed == 7
    assert enforced.commands_completed == 7
    assert enforced.partial_change is True
    assert enforced.partial_mutation is True
    assert enforced.reconcile_required is True
    assert enforced.retry_safe is False
    assert public.status == "partial_failure"
    assert public.verified is False
    assert public.commands_executed == 7
    assert public.reconcile_required is True


def test_reconcile_existing_routes_to_sync_and_preserves_failed_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_calls: list[str] = []
    result = _operate(
        monkeypatch,
        _repeat_result(
            verified=False,
            verification_error="동기화 후 그림 사후 검증 실패",
        ),
        reconcile_existing=True,
        route_calls=route_calls,
    )

    assert route_calls == ["sync"]
    assert result.status == "partial_change"
    assert result.verified is False
    assert result.failed_step == "native_structure_readback"
    assert result.reconcile_required is True


def test_unverified_repeat_is_not_reexecuted_with_the_same_operation_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    operation_id = "repeat-readback-failed"
    inputs = HwpOperateInputs(
        request_id=operation_id,
        operation="table.repeat_template",
        recipe=HwpPriorityRecipeInputs(table_template=_plan()),
    )
    document = OpenDocument(
        selector="active",
        title="repeat.hwp",
        full_name="C:/documents/repeat.hwp",
        document_id=17,
        format="HWP",
        edit_mode=1,
        modified=False,
        page_count=1,
        active=True,
        window_handle=101,
    )
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "journal"))
    prepared = idempotency.prepare(document, "표를 두 번 반복", inputs, None)
    assert isinstance(prepared, OperationTicket)
    failed = _operate(
        monkeypatch,
        _repeat_result(
            verified=False,
            verification_error="created-table 표를 사후 조회에서 찾지 못했습니다",
        ),
    ).model_copy(update={"request_id": operation_id})

    committed = idempotency.commit(prepared, failed)
    repeated = idempotency.prepare(document, "표를 두 번 반복", inputs, None)

    assert committed.status == "partial_change"
    assert committed.reconcile_required is True
    assert committed.retry_safe is False
    assert isinstance(repeated, OperationResult)
    assert repeated.status == "partial_change"
    assert repeated.idempotency_status == "failed"
    assert repeated.commands_executed == 7
    assert repeated.reconcile_required is True
