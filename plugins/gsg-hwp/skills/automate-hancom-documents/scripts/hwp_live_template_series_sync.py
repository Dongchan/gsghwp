from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import (
    CaptureTableCommand,
    DeleteControlCommand,
    NativeActionCommand,
    NativeActionRequest,
    NativeActionResult,
    NativeDetailedCaption,
    RunCommand,
    SelectControlCommand,
)
from hwp_live_native_batch import execute_native_actions, inspect_native_structure
from hwp_live_native_template_repeat import (
    NativeCellPicture,
    NativeCellText,
    NativeTableCopy,
    caption_literal_text,
    copy_caption_title,
    replace_table_cell_pictures_commands,
    replace_table_caption_commands,
    table_copy_content_commands,
)
from hwp_live_template_repeat import (
    TableTemplateRepeatPlan,
    TableTemplateRepeatResult,
    TemplateTableBlock,
    repeat_table_template,
)
from hwp_live_template_series_discovery import (
    DiscoveredSeriesTable,
    discover_table_series as discover_table_series,
    required_series_snapshot as _required_snapshot,
)


def _native_block(
    block: TemplateTableBlock,
    *,
    source_rows: int,
    target_rows: int,
) -> NativeTableCopy:
    expected_rows = source_rows - block.delete_row_count
    if target_rows == source_rows:
        delete_from = block.delete_rows_from
        delete_count = block.delete_row_count
    elif target_rows == expected_rows:
        delete_from = None
        delete_count = 0
    else:
        raise HwpLiveError(
            f"기존 시리즈 표 행 수가 템플릿과 다릅니다: {target_rows}"
        )
    return NativeTableCopy(
        text_cells=tuple(
            NativeCellText(cell.address, cell.replacement) for cell in block.text_cells
        ),
        pictures=tuple(
            NativeCellPicture(
                image.address,
                image.path,
                image.width_mm,
                image.height_mm,
            )
            for image in block.images
        ),
        delete_rows_from=delete_from,
        delete_row_count=delete_count,
    )


def execute_series_actions(
    window_handle: int,
    commands: tuple[NativeActionCommand, ...],
) -> NativeActionResult:
    before = _required_snapshot(window_handle)
    result = execute_native_actions(
        window_handle,
        NativeActionRequest(
            before.document_id,
            before.full_name,
            commands,
            atomic=False,
        ),
        minimum_version=4,
    )
    if result is None:
        raise HwpLiveError("표 시리즈 네이티브 배치를 실행할 수 없습니다")
    return result


def _required_caption(
    window_handle: int,
    target: DiscoveredSeriesTable,
) -> NativeDetailedCaption:
    inspected = inspect_native_structure(window_handle, target.page.page)
    if inspected is None:
        raise HwpLiveError(f"{target.page.page}쪽 캡션 상세 구조를 읽지 못했습니다")
    captions = tuple(
        caption
        for caption in inspected.captions
        if caption.table_instance_id == target.control.instance_id
    )
    if len(captions) != 1:
        raise HwpLiveError(
            f"{target.page.page}쪽 대상 표의 캡션을 하나로 확인하지 못했습니다"
        )
    return captions[0]


def _series_captions(
    window_handle: int,
    targets: tuple[DiscoveredSeriesTable, ...],
) -> tuple[NativeDetailedCaption, ...]:
    captions = tuple(_required_caption(window_handle, target) for target in targets)
    baseline = captions[0]
    baseline_metadata = (
        baseline.automatic_number,
        baseline.style_id,
        baseline.style_name,
    )
    for target, caption in zip(targets[1:], captions[1:], strict=True):
        if (
            caption.automatic_number,
            caption.style_id,
            caption.style_name,
        ) != baseline_metadata:
            raise HwpLiveError(
                f"{target.page.page}쪽 캡션이 첫 표의 자동번호·스타일과 다릅니다"
            )
    return captions


def _verify_captions(
    window_handle: int,
    targets: tuple[DiscoveredSeriesTable, ...],
    caption_title: str | None,
) -> None:
    if caption_title is None:
        return
    captions = _series_captions(window_handle, targets)
    for index, (target, caption) in enumerate(
        zip(targets, captions, strict=True)
    ):
        actual = caption_literal_text(
            caption.text,
            automatic_number=caption.automatic_number,
        )
        expected = copy_caption_title(caption_title, index)
        if actual != expected:
            raise HwpLiveError(
                f"{target.page.page}쪽 캡션 제목이 요청과 다릅니다: {actual!r}"
            )


def _verify_target(
    target: DiscoveredSeriesTable,
    block: TemplateTableBlock,
) -> None:
    page = target.page
    cells = {
        cell.address: cell
        for cell in page.cells
        if cell.table_instance_id == target.control.instance_id
    }
    for expected in block.text_cells:
        actual = cells.get(expected.address)
        if actual is None or actual.text.rstrip("\r\n") != expected.replacement:
            raise HwpLiveError(
                f"{target.page.page}쪽 {expected.address} 셀 동기화를 확인하지 못했습니다"
            )
    for image in block.images:
        cell = cells.get(image.address)
        if cell is None:
            raise HwpLiveError(
                f"{target.page.page}쪽 {image.address} 그림 셀을 확인하지 못했습니다"
            )
        pictures = tuple(
            control
            for control in page.controls
            if control.control_type == "gso" and control.anchor.list_id == cell.list_id
        )
        if len(pictures) != 1:
            raise HwpLiveError(
                f"{target.page.page}쪽 {image.address} 그림이 하나가 아닙니다"
            )


def _remove_source_pictures(
    window_handle: int,
    target: DiscoveredSeriesTable,
    block: TemplateTableBlock,
) -> NativeActionResult | None:
    image_addresses = {image.address for image in block.images}
    list_ids = {
        cell.list_id
        for cell in target.page.cells
        if cell.table_instance_id == target.control.instance_id
        and cell.address in image_addresses
    }
    controls = tuple(
        control
        for control in target.page.controls
        if control.control_type == "gso" and control.anchor.list_id in list_ids
    )
    if not controls:
        return None
    return execute_series_actions(
        window_handle,
        tuple(DeleteControlCommand(control.instance_id) for control in controls),
    )


def sync_table_template_series(
    window_handle: int,
    plan: TableTemplateRepeatPlan,
) -> TableTemplateRepeatResult:
    targets = discover_table_series(window_handle, plan)
    if len(targets) == 1 and len(plan.blocks) > 1:
        cleaned = _remove_source_pictures(window_handle, targets[0], plan.blocks[0])
        repeated = repeat_table_template(window_handle, plan)
        verified = discover_table_series(window_handle, plan)
        if len(verified) != len(plan.blocks):
            raise HwpLiveError("생성된 시리즈 표 수가 원본 레코드 수와 다릅니다")
        for target, block in zip(verified, plan.blocks, strict=True):
            _verify_target(target, block)
        _verify_captions(window_handle, verified, plan.caption_title)
        repeated = repeated.model_copy(
            update={"verified": True, "verification_error": None}
        )
        if cleaned is None:
            return repeated
        return repeated.model_copy(
            update={
                "commands_executed": repeated.commands_executed
                + cleaned.commands_executed,
                "native_elapsed_microseconds": repeated.native_elapsed_microseconds
                + cleaned.elapsed_microseconds,
                "content_elapsed_microseconds": repeated.content_elapsed_microseconds
                + cleaned.elapsed_microseconds,
            }
        )
    if len(targets) != len(plan.blocks):
        raise HwpLiveError(
            "기존 시리즈 표는 원본 한 개이거나 필요한 개수와 정확히 같아야 합니다"
        )
    source_rows = targets[0].control.rows
    if source_rows is None:
        raise HwpLiveError("동기화 원본 표 행 수를 읽지 못했습니다")
    commands: list[NativeActionCommand] = []
    native_blocks: list[NativeTableCopy] = []
    captions = (
        _series_captions(window_handle, targets)
        if plan.caption_title is not None
        else ()
    )
    for index, (target, block) in enumerate(
        zip(targets, plan.blocks, strict=True)
    ):
        if target.control.rows is None:
            raise HwpLiveError("시리즈 표 행 수를 읽지 못했습니다")
        native = _native_block(
            block,
            source_rows=source_rows,
            target_rows=target.control.rows,
        )
        native_blocks.append(native)
        if plan.caption_title is not None:
            caption = captions[index]
            existing_title = caption_literal_text(
                caption.text,
                automatic_number=caption.automatic_number,
            )
            desired_title = copy_caption_title(plan.caption_title, index)
            if existing_title != desired_title:
                commands.extend(
                    replace_table_caption_commands(
                        target.control.instance_id,
                        existing_title,
                        desired_title,
                    )
                )
        commands.extend(
            (
                SelectControlCommand(target.control.instance_id),
                CaptureTableCommand(),
                RunCommand("ShapeObjTableSelCell"),
            )
        )
        commands.extend(
            table_copy_content_commands(
                NativeTableCopy(
                    text_cells=native.text_cells,
                    delete_rows_from=native.delete_rows_from,
                    delete_row_count=native.delete_row_count,
                )
            )
        )
        commands.extend(
            replace_table_cell_pictures_commands(
                target.page,
                target.control.instance_id,
                native.pictures,
            )
        )
    executed = execute_series_actions(window_handle, tuple(commands))
    verified = discover_table_series(window_handle, plan)
    if tuple(target.control.instance_id for target in verified) != tuple(
        target.control.instance_id for target in targets
    ):
        raise HwpLiveError("동기화 후 시리즈 표 순서가 기존 표와 다릅니다")
    for target, block in zip(verified, plan.blocks, strict=True):
        _verify_target(target, block)
    _verify_captions(window_handle, verified, plan.caption_title)
    after = _required_snapshot(window_handle)
    return TableTemplateRepeatResult(
        source_control_id=plan.source_control_id,
        created_control_ids=(),
        table_count=len(plan.blocks),
        text_cell_count=sum(len(block.text_cells) for block in plan.blocks),
        image_count=sum(len(block.images) for block in plan.blocks),
        commands_executed=executed.commands_executed,
        native_elapsed_microseconds=executed.elapsed_microseconds,
        current_page=after.current_page,
        page_count=after.page_count,
        # 동기화 대상 표가 실제로 있던 쪽. discover_table_series 결과를 다시 쓰므로
        # 네이티브 왕복은 늘지 않는다.
        affected_pages=tuple(sorted({target.page for target in verified})),
        modified=after.modified,
        verified=True,
        verification_error=None,
        content_elapsed_microseconds=executed.elapsed_microseconds,
        image_timing_count=executed.image_timing_count,
        image_max_microseconds=executed.image_max_microseconds,
        image_total_microseconds=executed.image_total_microseconds,
    )
