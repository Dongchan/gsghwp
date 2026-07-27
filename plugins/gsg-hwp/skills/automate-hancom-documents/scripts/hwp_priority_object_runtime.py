from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import (
    CaptureTableCommand,
    InsertTextCommand,
    NativeActionCommand,
    NativeActionRequest,
    NativeDetailedControl,
    NativePageInspection,
    NativePosition,
    RunCommand,
    SelectControlCommand,
)
from hwp_live_native_batch import execute_native_actions, read_native_snapshot
from hwp_live_native_text_format import style_command
from hwp_live_rot import HwpDocumentCandidate
from hwp_operation_certification import certified_recipe
from hwp_operation_contract import (
    OperationResult,
    OperationStatus,
    WorkflowResolution,
)
from hwp_operation_registry import operation_registry


_PICTURE_CONTROL_TYPES: Final = frozenset(("gso", "pic", "picture"))
_HWPUNITS_PER_MILLIMETER: Final = 7_200 / 25.4
_BRACKETED_PICTURE_CAPTION: Final = re.compile(
    r"^(?P<prefix>\s*(?:\(\s*그림\b[^)\r\n]*\)"
    + r"|\[\s*그림\b[^\]\r\n]*\]|<\s*그림\b[^>\r\n]*>))(?P<literal>.*)$"
)
_PLAIN_PICTURE_CAPTION: Final = re.compile(
    r"^(?P<prefix>\s*그림\s+\d+(?:[.\-]\d+)*(?:\s+|$))(?P<literal>.*)$"
)


@dataclass(frozen=True, slots=True)
class PictureInsertExpectation:
    fitted_width_mm: float | None
    fitted_height_mm: float | None
    document_end_pages: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class PictureReplaceExpectation:
    control_id: str
    fitted_width_mm: float
    fitted_height_mm: float


@dataclass(frozen=True, slots=True)
class VerifiedPicture:
    instance_id: str
    width_mm: float
    height_mm: float


@dataclass(frozen=True, slots=True)
class PictureCaptionExpectation:
    control_id: str
    caption_text: str
    anchor: NativePosition
    existing_auto_number_count: int
    automatic_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class PictureCaptionText:
    automatic_prefix: str
    literal_text: str


@dataclass(frozen=True, slots=True)
class PictureCaptionProbe:
    caption: PictureCaptionText | None
    commands_executed: int
    elapsed_microseconds: int


def _picture_controls(
    controls: tuple[NativeDetailedControl, ...],
) -> dict[str, NativeDetailedControl]:
    return {
        control.instance_id: control
        for control in controls
        if control.control_type in _PICTURE_CONTROL_TYPES and control.instance_id
    }


def _matches_dimension(actual: int | None, expected_mm: float) -> bool:
    if actual is None:
        return False
    expected = round(expected_mm * _HWPUNITS_PER_MILLIMETER)
    tolerance = max(2, round(abs(expected) * 0.005))
    return abs(actual - expected) <= tolerance


def parse_picture_caption_selection(selected_text: str) -> PictureCaptionText | None:
    text = selected_text.rstrip("\r\n")
    matched = _BRACKETED_PICTURE_CAPTION.fullmatch(text)
    if matched is None:
        matched = _PLAIN_PICTURE_CAPTION.fullmatch(text)
    if matched is None:
        return None
    return PictureCaptionText(
        automatic_prefix=matched.group("prefix"),
        literal_text=matched.group("literal"),
    )


def probe_picture_caption(
    candidate: HwpDocumentCandidate,
    control_id: str,
) -> PictureCaptionProbe:
    if not control_id:
        raise HwpLiveError("그림 캡션을 확인할 개체 ID가 비어 있습니다")
    native = execute_native_actions(
        candidate.window_handle,
        NativeActionRequest(
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            commands=(
                SelectControlCommand(control_id),
                CaptureTableCommand(),
                RunCommand("ShapeObjAttachCaption"),
                RunCommand("MoveParaBegin"),
                RunCommand("MoveSelParaEnd"),
            ),
        ),
        minimum_version=9,
    )
    if native is None:
        raise HwpLiveError("한컴 프로토콜 9 그림 캡션 확인 경로를 사용할 수 없습니다")
    snapshot = read_native_snapshot(candidate.window_handle)
    if snapshot is None:
        raise HwpLiveError("그림 캡션 선택 결과를 읽지 못했습니다")
    if (
        snapshot.document_id != candidate.document.DocumentID
        or snapshot.full_name != candidate.document.FullName
    ):
        raise HwpLiveError("그림 캡션 확인 결과가 대상 문서와 다릅니다")
    return PictureCaptionProbe(
        caption=parse_picture_caption_selection(snapshot.selected_text),
        commands_executed=native.commands_executed,
        elapsed_microseconds=native.elapsed_microseconds,
    )


def picture_caption_commands(
    control_id: str,
    caption: PictureCaptionText | None,
    *,
    style_id: int,
    title: str,
) -> tuple[NativeActionCommand, ...]:
    if not control_id:
        raise HwpLiveError("그림 캡션 대상의 개체 ID가 비어 있습니다")
    if not title:
        raise HwpLiveError("그림 캡션 제목이 비어 있습니다")
    enter_caption: tuple[NativeActionCommand, ...] = (
        SelectControlCommand(control_id),
        CaptureTableCommand(),
        RunCommand("ShapeObjAttachCaption"),
        RunCommand("MoveParaEnd"),
    )
    if caption is None:
        return (
            *enter_caption,
            InsertTextCommand(title),
            RunCommand("SelectAll"),
            style_command(style_id),
            RunCommand("CloseEx"),
        )
    delete_literal: tuple[NativeActionCommand, ...] = (
        (
            *(RunCommand("MoveSelLeft") for _ in caption.literal_text),
            RunCommand("Delete"),
        )
        if caption.literal_text
        else ()
    )
    return (
        *enter_caption,
        *delete_literal,
        InsertTextCommand(title),
        RunCommand("CloseEx"),
    )


def verify_inserted_picture(
    before: tuple[NativeDetailedControl, ...],
    after: tuple[NativeDetailedControl, ...],
    expectation: PictureInsertExpectation,
) -> VerifiedPicture:
    previous_ids = _picture_controls(before).keys()
    created = tuple(
        control
        for instance_id, control in _picture_controls(after).items()
        if instance_id not in previous_ids
    )
    if len(created) != 1:
        raise HwpLiveError(
            f"그림 삽입 후 새 그림을 정확히 하나 확인하지 못했습니다: {len(created)}개"
        )
    picture = created[0]
    if (
        expectation.fitted_width_mm is not None
        and not _matches_dimension(
            picture.width_hwpunit,
            expectation.fitted_width_mm,
        )
    ) or (
        expectation.fitted_height_mm is not None
        and not _matches_dimension(
            picture.height_hwpunit,
            expectation.fitted_height_mm,
        )
    ):
        raise HwpLiveError(
            "삽입된 그림 크기가 원본 비율을 유지한 요청 상자 맞춤 크기와 다릅니다"
        )
    if picture.width_hwpunit is None or picture.height_hwpunit is None:
        raise HwpLiveError("삽입된 그림의 실제 크기를 상세 구조에서 읽지 못했습니다")
    if expectation.document_end_pages and not any(
        picture.page_start <= page <= picture.page_end
        for page in expectation.document_end_pages
    ):
        raise HwpLiveError("삽입된 그림이 문서 끝의 기존 또는 새 마지막 쪽에 없습니다")
    return VerifiedPicture(
        instance_id=picture.instance_id,
        width_mm=picture.width_hwpunit / _HWPUNITS_PER_MILLIMETER,
        height_mm=picture.height_hwpunit / _HWPUNITS_PER_MILLIMETER,
    )


def verify_replaced_picture(
    before: NativeDetailedControl,
    after: NativeDetailedControl,
    expectation: PictureReplaceExpectation,
) -> VerifiedPicture:
    if (
        before.control_type not in _PICTURE_CONTROL_TYPES
        or after.control_type not in _PICTURE_CONTROL_TYPES
        or before.instance_id != expectation.control_id
        or after.instance_id != expectation.control_id
        or after.anchor != before.anchor
    ):
        raise HwpLiveError("그림 교체 후 같은 그림 개체와 앵커를 확인하지 못했습니다")
    if not _matches_dimension(
        after.width_hwpunit,
        expectation.fitted_width_mm,
    ) or not _matches_dimension(
        after.height_hwpunit,
        expectation.fitted_height_mm,
    ):
        raise HwpLiveError(
            "교체된 그림 크기가 원본 비율을 유지한 요청 상자 맞춤 크기와 다릅니다"
        )
    if after.width_hwpunit is None or after.height_hwpunit is None:
        raise HwpLiveError("교체된 그림의 실제 크기를 상세 구조에서 읽지 못했습니다")
    return VerifiedPicture(
        instance_id=after.instance_id,
        width_mm=after.width_hwpunit / _HWPUNITS_PER_MILLIMETER,
        height_mm=after.height_hwpunit / _HWPUNITS_PER_MILLIMETER,
    )


def verify_picture_caption(
    after: NativePageInspection,
    expectation: PictureCaptionExpectation,
) -> str:
    targets = tuple(
        control
        for control in after.controls
        if control.instance_id == expectation.control_id
        and control.control_type in _PICTURE_CONTROL_TYPES
    )
    if len(targets) != 1 or targets[0].anchor != expectation.anchor:
        raise HwpLiveError("그림 캡션 대상의 개체 ID와 앵커를 다시 확인하지 못했습니다")
    auto_number_count = sum(
        control.control_type == "atno" for control in after.controls
    )
    if expectation.automatic_prefix is None:
        if expectation.caption_text not in after.text:
            raise HwpLiveError(
                "그림 캡션 텍스트를 쪽 읽기 결과에서 확인하지 못했습니다"
            )
        if auto_number_count != expectation.existing_auto_number_count + 1:
            raise HwpLiveError(
                "그림 캡션의 새 자동 번호 조판 부호를 정확히 하나 확인하지 못했습니다"
            )
    else:
        expected_caption = f"{expectation.automatic_prefix}{expectation.caption_text}"
        if expected_caption not in after.text:
            raise HwpLiveError(
                "그림 캡션의 기존 자동 번호와 수정한 텍스트를 함께 확인하지 못했습니다"
            )
        if auto_number_count != expectation.existing_auto_number_count:
            raise HwpLiveError(
                "그림 캡션 수정 중 자동 번호 조판 부호 수가 변경되었습니다"
            )
    return targets[0].instance_id


def object_recipe_result(
    resolution: WorkflowResolution,
    status: OperationStatus,
    message: str,
    *,
    required_inputs: tuple[str, ...] = (),
) -> OperationResult:
    workflow = resolution.workflow_id
    recipe = None if workflow is None else certified_recipe(workflow)
    return OperationResult(
        status=status,
        query=resolution.query,
        registry_entries=operation_registry().count,
        lookup_microseconds=resolution.lookup_microseconds,
        workflow_candidates=resolution.candidates,
        required_inputs=required_inputs,
        message=message,
        recipe_id=None if recipe is None else f"recipe:{recipe.recipe_id}",
        recipe_steps=resolution.steps,
    )


def execute_object_commands(
    candidate: HwpDocumentCandidate,
    commands: tuple[NativeActionCommand, ...],
) -> tuple[int, int, int, int, bool]:
    native = execute_native_actions(
        candidate.window_handle,
        NativeActionRequest(
            document_id=candidate.document.DocumentID,
            full_name=candidate.document.FullName,
            commands=commands,
        ),
        minimum_version=9,
    )
    if native is None:
        raise HwpLiveError("한컴 프로토콜 9 네이티브 개체 recipe를 사용할 수 없습니다")
    after = read_native_snapshot(candidate.window_handle)
    if after is None:
        raise HwpLiveError("한컴 네이티브 개체 작업 결과를 읽지 못했습니다")
    return (
        native.commands_executed,
        native.elapsed_microseconds,
        after.current_page,
        after.page_count,
        after.modified,
    )
