from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import (
    CaptureTableCommand,
    NativeActionCommand,
    NativeActionRequest,
    NativeActionResult,
    NativePageCell,
    NativePageControl,
    NativePageInspection,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
    RunCommand,
    SelectControlCommand,
)
from hwp_live_native_batch import (
    execute_native_actions,
    inspect_native_page,
    inspect_native_pages,
    read_native_snapshot,
)
from hwp_live_native_template_repeat import (
    NativeCellPicture,
    NativeCellText,
    NativeCaptionProfile,
    NativeTableCopy,
    caption_display_number,
    clone_table_after_source_commands,
    copy_caption_title,
    table_copy_content_commands,
)
from hwp_live_values import ContractModel


def _address(value: str) -> str:
    normalized = value.strip().upper()
    if re.fullmatch(r"[A-Z]+[1-9][0-9]*", normalized) is None:
        raise ValueError("cell address must look like B3")
    return normalized


def _row(address: str) -> int:
    return int("".join(character for character in address if character.isdigit()))


class TemplateTextCell(ContractModel):
    address: str = Field(max_length=20)
    expected_text: str = Field(max_length=200_000)
    replacement: str = Field(max_length=200_000)

    @field_validator("address")
    @classmethod
    def normalize_address(cls, value: str) -> str:
        return _address(value)


class TemplateImageCell(ContractModel):
    address: str = Field(max_length=20)
    expected_text: str = Field(max_length=200_000)
    path: Path
    width_mm: float = Field(gt=0, le=1_000)
    height_mm: float = Field(gt=0, le=1_000)

    @field_validator("address")
    @classmethod
    def normalize_address(cls, value: str) -> str:
        return _address(value)


class TemplateTableBlock(ContractModel):
    text_cells: tuple[TemplateTextCell, ...] = Field(default=(), max_length=500)
    images: tuple[TemplateImageCell, ...] = Field(default=(), max_length=200)
    delete_rows_from: str | None = Field(default=None, max_length=20)
    delete_row_count: int = Field(default=0, ge=0, le=1_000)

    @field_validator("delete_rows_from")
    @classmethod
    def normalize_optional_address(cls, value: str | None) -> str | None:
        return None if value is None else _address(value)

    @model_validator(mode="after")
    def validate_block(self) -> Self:
        text_addresses = tuple(cell.address for cell in self.text_cells)
        image_addresses = tuple(cell.address for cell in self.images)
        if len(text_addresses) != len(set(text_addresses)):
            raise ValueError("text cell addresses must be unique within a block")
        if len(image_addresses) != len(set(image_addresses)):
            raise ValueError("image cell addresses must be unique within a block")
        if set(text_addresses) & set(image_addresses):
            raise ValueError("one cell cannot receive text and an image in the same block")
        if (self.delete_rows_from is None) != (self.delete_row_count == 0):
            raise ValueError("row deletion start and count must be provided together")
        if self.delete_rows_from is not None:
            first_deleted = _row(self.delete_rows_from)
            if any(
                _row(address) >= first_deleted
                for address in (*text_addresses, *image_addresses)
            ):
                raise ValueError("a block cannot fill cells in rows it deletes")
        return self


class TableTemplateRepeatPlan(ContractModel):
    source_page: int = Field(ge=1)
    source_control_id: str = Field(min_length=1, max_length=100)
    caption_title: str | None = Field(default=None, min_length=1, max_length=2_000)
    blocks: tuple[TemplateTableBlock, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_one_template_value_per_address(self) -> Self:
        expected: dict[str, str] = {}
        for block in self.blocks:
            for cell in (*block.text_cells, *block.images):
                previous = expected.setdefault(cell.address, cell.expected_text)
                if previous != cell.expected_text:
                    raise ValueError(
                        f"template cell {cell.address} has conflicting expected text"
                    )
        return self


class TableTemplateRepeatResult(ContractModel):
    source_control_id: str
    created_control_ids: tuple[str, ...]
    table_count: int = Field(ge=1)
    text_cell_count: int = Field(ge=0)
    image_count: int = Field(ge=0)
    commands_executed: int = Field(ge=0)
    native_elapsed_microseconds: int = Field(ge=0)
    current_page: int = Field(ge=1)
    page_count: int = Field(ge=1)
    modified: bool
    caption_profile_elapsed_microseconds: int = Field(default=0, ge=0)
    clone_elapsed_microseconds: int = Field(default=0, ge=0)
    caption_elapsed_microseconds: int = Field(default=0, ge=0)
    content_elapsed_microseconds: int = Field(default=0, ge=0)
    image_timing_count: int = Field(default=0, ge=0)
    image_max_microseconds: int = Field(default=0, ge=0)
    image_total_microseconds: int = Field(default=0, ge=0)


def _required_page(window_handle: int, page: int) -> NativePageInspection:
    inspected = inspect_native_page(window_handle, page)
    if inspected is None:
        raise HwpLiveError("한컴 네이티브 인프로세스 표 구조 조회를 사용할 수 없습니다")
    return inspected


def _required_action(
    window_handle: int,
    request: NativeActionRequest,
) -> NativeActionResult:
    result = execute_native_actions(window_handle, request, minimum_version=4)
    if result is None:
        raise HwpLiveError("한컴 네이티브 인프로세스 표 배치를 사용할 수 없습니다")
    return result


def _source_cells(
    inspected: NativePageInspection,
    source_control_id: str,
) -> dict[str, NativePageCell]:
    controls = tuple(
        control
        for control in inspected.controls
        if control.control_type == "tbl" and control.instance_id == source_control_id
    )
    if len(controls) != 1:
        raise HwpLiveError("지정 쪽에서 원본 표 개체 ID를 하나로 찾지 못했습니다")
    source_errors = tuple(
        error
        for error in inspected.inspection_errors
        if error.control_instance_id == source_control_id
    )
    if source_errors:
        raise HwpLiveError(f"원본 표 구조 조회 오류: {source_errors[0].message}")
    cells = {
        cell.address: cell
        for cell in inspected.cells
        if cell.table_instance_id == source_control_id
    }
    if not cells:
        raise HwpLiveError("원본 표의 셀 구조를 읽지 못했습니다")
    return cells


def _source_control(
    inspected: NativePageInspection,
    source_control_id: str,
) -> NativePageControl:
    controls = tuple(
        control
        for control in inspected.controls
        if control.control_type == "tbl" and control.instance_id == source_control_id
    )
    if len(controls) != 1:
        raise HwpLiveError("지정 쪽에서 원본 표 개체 ID를 하나로 찾지 못했습니다")
    return controls[0]


def _caption_title_format_source(
    selection: NativeSelection,
    caption_title: str,
) -> NativePosition:
    title_start = selection.end.character - len(caption_title)
    if (
        not selection.selected
        or selection.start.list_id != selection.end.list_id
        or selection.start.paragraph != selection.end.paragraph
        or title_start < selection.start.character
    ):
        raise HwpLiveError("원본 표 캡션의 내용 서식 위치를 계산하지 못했습니다")
    return NativePosition(
        selection.end.list_id,
        selection.end.paragraph,
        title_start,
    )


def _verify_repeated_caption_numbers(
    pages: tuple[NativePageInspection, ...],
    created_control_ids: tuple[str, ...],
    caption_title: str,
    source_display_number: int,
) -> None:
    page_by_control = {
        control.instance_id: page
        for page in pages
        for control in page.controls
        if control.instance_id in created_control_ids
    }
    missing = tuple(
        control_id
        for control_id in created_control_ids
        if control_id not in page_by_control
    )
    if missing:
        raise HwpLiveError("복제 표의 실제 쪽을 찾아 캡션을 검증하지 못했습니다")
    for offset, control_id in enumerate(created_control_ids, start=1):
        expected_title = copy_caption_title(caption_title, offset)
        expected_number = source_display_number + offset
        actual_number = caption_display_number(
            page_by_control[control_id].text,
            expected_title,
        )
        if actual_number != expected_number:
            message = "복제 표 캡션의 표시 번호 또는 제목이 요청과 다릅니다: expected={}, actual={}".format(
                expected_number,
                actual_number,
            )
            raise HwpLiveError(message)


def _read_source_caption_profile(
    window_handle: int,
    before: NativeSnapshot,
    source: NativePageControl,
    caption_title: str,
) -> tuple[NativeCaptionProfile, tuple[NativeActionResult, NativeActionResult]]:
    entered = _required_action(
        window_handle,
        NativeActionRequest(
            document_id=before.document_id,
            full_name=before.full_name,
            commands=(
                SelectControlCommand(source.instance_id),
                RunCommand("ShapeObjAttachCaption"),
                RunCommand("MoveSelParaEnd"),
            ),
        ),
    )
    selected = read_native_snapshot(window_handle)
    try:
        if selected is None:
            raise HwpLiveError("원본 표 캡션의 네이티브 서식을 읽지 못했습니다")
        selection = selected.selection
        if (
            not selection.selected
            or selection.start.list_id == source.anchor.list_id
            or selection.start.list_id != selection.end.list_id
            or selection.start.paragraph != selection.end.paragraph
            or not selected.selected_text.rstrip().endswith(caption_title)
        ):
            raise HwpLiveError("원본 표에서 지정한 캡션 제목과 실제 캡션을 일치시켜 읽지 못했습니다")
        profile = NativeCaptionProfile(
            selected.style_id,
            selected.character_format,
            selected.paragraph_format,
            _caption_title_format_source(selection, caption_title),
            automatic_number=(
                selection.end.character - selection.start.character
                > len(selected.selected_text.rstrip())
            ),
        )
    finally:
        closed = _required_action(
            window_handle,
            NativeActionRequest(
                document_id=before.document_id,
                full_name=before.full_name,
                commands=(RunCommand("CloseEx"),),
            ),
        )
    return profile, (entered, closed)


def _native_blocks(
    plan: TableTemplateRepeatPlan,
    source: dict[str, NativePageCell],
    inspected: NativePageInspection,
) -> tuple[NativeTableCopy, ...]:
    expected: dict[str, str] = {}
    image_addresses: set[str] = set()
    converted: list[NativeTableCopy] = []
    for block in plan.blocks:
        text_cells: list[NativeCellText] = []
        pictures: list[NativeCellPicture] = []
        for cell in block.text_cells:
            expected[cell.address] = cell.expected_text
            text_cells.append(NativeCellText(cell.address, cell.replacement))
        for image in block.images:
            expected[image.address] = image.expected_text
            image_addresses.add(image.address)
            pictures.append(
                NativeCellPicture(
                    image.address,
                    image.path,
                    image.width_mm,
                    image.height_mm,
                )
            )
        converted.append(
            NativeTableCopy(
                text_cells=tuple(text_cells),
                pictures=tuple(pictures),
                delete_rows_from=block.delete_rows_from,
                delete_row_count=block.delete_row_count,
            )
        )
    missing = tuple(address for address in expected if address not in source)
    if missing:
        raise HwpLiveError(f"원본 표에 반복 배치 셀이 없습니다: {', '.join(missing)}")
    stale = tuple(
        address
        for address, value in expected.items()
        if source[address].text.removesuffix("\r\n") != value
    )
    if stale:
        raise HwpLiveError(f"원본 표 셀 내용이 조회 조건과 다릅니다: {', '.join(stale)}")
    image_lists = {source[address].list_id for address in image_addresses}
    existing_objects = tuple(
        control.instance_id
        for control in inspected.controls
        if control.control_type == "gso" and control.anchor.list_id in image_lists
    )
    if existing_objects:
        raise HwpLiveError(
            "원본 표 그림 셀에 기존 개체가 있어 중복 삽입을 중단했습니다"
        )
    return tuple(converted)


def repeat_table_template(
    window_handle: int,
    plan: TableTemplateRepeatPlan,
) -> TableTemplateRepeatResult:
    inspected = _required_page(window_handle, plan.source_page)
    source_control = _source_control(inspected, plan.source_control_id)
    source = _source_cells(inspected, plan.source_control_id)
    if plan.caption_title is not None and plan.caption_title not in inspected.text:
        raise HwpLiveError("원본 표 쪽에서 지정한 캡션 제목을 찾지 못했습니다")
    blocks = _native_blocks(plan, source, inspected)
    before = read_native_snapshot(window_handle)
    if before is None:
        raise HwpLiveError("한컴 네이티브 현재 문서 상태를 읽지 못했습니다")
    created: tuple[str, ...] = ()
    stage_results: list[NativeActionResult] = []
    caption_profile_elapsed_microseconds = 0
    clone_elapsed_microseconds = 0
    caption_elapsed_microseconds = 0
    caption_profile: NativeCaptionProfile | None = None
    if plan.caption_title is not None:
        caption_profile, caption_results = _read_source_caption_profile(
            window_handle,
            before,
            source_control,
            plan.caption_title,
        )
        display_number = caption_display_number(
            inspected.text,
            plan.caption_title,
        )
        caption_profile = replace(
            caption_profile,
            automatic_number=display_number is not None,
            display_number=display_number,
        )
        stage_results.extend(caption_results)
        caption_profile_elapsed_microseconds = sum(
            result.elapsed_microseconds for result in caption_results
        )
    if len(blocks) > 1:
        clone_request = NativeActionRequest(
            document_id=before.document_id,
            full_name=before.full_name,
            commands=clone_table_after_source_commands(
                plan.source_control_id,
                len(blocks) - 1,
                caption_title=plan.caption_title,
                caption_profile=caption_profile,
            ),
        )
        cloned = _required_action(window_handle, clone_request)
        created = cloned.created_control_ids
        if len(created) != len(blocks) - 1:
            raise HwpLiveError("복제된 표 개체 수가 요청한 반복 수와 다릅니다")
        stage_results.append(cloned)
        clone_elapsed_microseconds = cloned.elapsed_microseconds
    table_ids = (plan.source_control_id, *created)
    fill_commands: list[NativeActionCommand] = []
    for table_id, block in zip(table_ids, blocks, strict=True):
        fill_commands.extend(
            (
                SelectControlCommand(table_id),
                CaptureTableCommand(),
                RunCommand("ShapeObjTableSelCell"),
            )
        )
        fill_commands.extend(table_copy_content_commands(block))
    filled = _required_action(
        window_handle,
        NativeActionRequest(
            document_id=before.document_id,
            full_name=before.full_name,
            commands=tuple(fill_commands),
        ),
    )
    stage_results.append(filled)
    after = read_native_snapshot(window_handle)
    if after is None:
        raise HwpLiveError("반복 표 배치 후 한컴 문서 상태를 읽지 못했습니다")
    if (
        plan.caption_title is not None
        and caption_profile is not None
        and caption_profile.display_number is not None
        and created
    ):
        verification_pages = inspect_native_pages(
            window_handle,
            tuple(range(plan.source_page, after.current_page + 1)),
            include_cells=False,
        )
        _verify_repeated_caption_numbers(
            verification_pages,
            created,
            plan.caption_title,
            caption_profile.display_number,
        )
    return TableTemplateRepeatResult(
        source_control_id=plan.source_control_id,
        created_control_ids=created,
        table_count=len(blocks),
        text_cell_count=sum(len(block.text_cells) for block in plan.blocks),
        image_count=sum(len(block.images) for block in plan.blocks),
        commands_executed=sum(result.commands_executed for result in stage_results),
        native_elapsed_microseconds=sum(
            result.elapsed_microseconds for result in stage_results
        ),
        current_page=after.current_page,
        page_count=after.page_count,
        modified=after.modified,
        caption_profile_elapsed_microseconds=caption_profile_elapsed_microseconds,
        clone_elapsed_microseconds=clone_elapsed_microseconds,
        caption_elapsed_microseconds=caption_elapsed_microseconds,
        content_elapsed_microseconds=filled.elapsed_microseconds,
        image_timing_count=sum(result.image_timing_count for result in stage_results),
        image_max_microseconds=max(
            (result.image_max_microseconds for result in stage_results),
            default=0,
        ),
        image_total_microseconds=sum(
            result.image_total_microseconds for result in stage_results
        ),
    )
