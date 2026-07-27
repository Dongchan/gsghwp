from __future__ import annotations

import ntpath
from collections.abc import Callable

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_caption_edit import attach_table_caption as attach_table_caption
from hwp_live_caption_format import (
    TableCaptionFormatSource as TableCaptionFormatSource,
    caption_format_sources as caption_format_sources,
    caption_text_uses_hierarchy,
    has_hierarchical_table_caption as has_hierarchical_table_caption,
)
from hwp_live_native_action_models import (
    NativeActionRequest,
    NativePosition,
    RunCommand,
    SelectControlCommand,
)
from hwp_live_native_batch import (
    execute_native_actions,
    inspect_native_structure,
    read_native_snapshot,
)
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_state_guard import preserved_live_state


def _is_candidate_document(
    candidate: HwpDocumentCandidate,
    document_id: int,
    full_name: str,
) -> bool:
    return document_id == candidate.document_id and ntpath.normcase(
        ntpath.normpath(full_name)
    ) == ntpath.normcase(ntpath.normpath(candidate.full_name))


def _read_attached_caption_source(
    candidate: HwpDocumentCandidate,
    source_id: str,
    expected_style_id: int,
    *,
    hierarchy_confirmed: bool,
) -> TableCaptionFormatSource | None:
    entered = execute_native_actions(
        candidate.window_handle,
        NativeActionRequest(
            document_id=candidate.document_id,
            full_name=candidate.full_name,
            commands=(
                SelectControlCommand(source_id),
                RunCommand("ShapeObjAttachCaption"),
                RunCommand("MoveParaBegin"),
                RunCommand("MoveSelParaEnd"),
            ),
        ),
        minimum_version=9,
    )
    if entered is None:
        raise HwpLiveError("계층형 표 캡션의 서식 원본 위치를 열 수 없습니다")
    snapshot = read_native_snapshot(candidate.window_handle)
    try:
        if snapshot is None:
            raise HwpLiveError("계층형 표 캡션의 서식 원본 위치를 읽지 못했습니다")
        selection = snapshot.selection
        if (
            not _is_candidate_document(
                candidate,
                snapshot.document_id,
                snapshot.full_name,
            )
            or snapshot.style_id != expected_style_id
            or not selection.selected
            or selection.start.list_id != selection.end.list_id
            or selection.start.paragraph != selection.end.paragraph
        ):
            raise HwpLiveError(
                "계층형 표 캡션의 스타일과 서식 원본 위치를 일치시켜 읽지 못했습니다"
            )
        if not hierarchy_confirmed and not has_hierarchical_table_caption(
            snapshot.selected_text
        ):
            return None
        return TableCaptionFormatSource(
            style_id=snapshot.style_id,
            position=selection.start,
        )
    finally:
        closed = execute_native_actions(
            candidate.window_handle,
            NativeActionRequest(
                document_id=candidate.document_id,
                full_name=candidate.full_name,
                commands=(RunCommand("CloseEx"),),
            ),
            minimum_version=9,
        )
        if closed is None:
            raise HwpLiveError("계층형 표 캡션 서식 원본 읽기를 끝내지 못했습니다")


def _read_preceding_caption_source(
    candidate: HwpDocumentCandidate,
    hwp: LiveHwpApplication,
    guard: Callable[[], None],
    *,
    page: int,
    table_positions: tuple[NativePosition, ...],
) -> TableCaptionFormatSource | None:
    for table_position in sorted(
        table_positions,
        key=lambda position: (
            position.list_id,
            position.paragraph,
            position.character,
        ),
        reverse=True,
    ):
        for paragraph in range(table_position.paragraph - 1, -1, -1):
            guard()
            if not hwp.set_pos(table_position.list_id, paragraph, 0):
                guard()
                continue
            guard()
            current_page = hwp.current_page
            guard()
            if current_page < page:
                break
            if current_page > page or not hwp.MoveParaBegin():
                guard()
                continue
            guard()
            _ = hwp.MoveSelParaEnd()
            guard()
            selection = hwp.get_selected_pos()
            guard()
            if not selection[0]:
                continue
            text = hwp.get_text_file(
                format="UNICODE",
                option="saveblock:true",
            )
            guard()
            if not has_hierarchical_table_caption(text):
                continue
            snapshot = read_native_snapshot(candidate.window_handle)
            if snapshot is None:
                raise HwpLiveError(
                    "계층형 표 제목 문단의 서식 원본 위치를 읽지 못했습니다"
                )
            native_selection = snapshot.selection
            if (
                not _is_candidate_document(
                    candidate,
                    snapshot.document_id,
                    snapshot.full_name,
                )
                or not native_selection.selected
                or native_selection.start.list_id != native_selection.end.list_id
                or native_selection.start.paragraph != native_selection.end.paragraph
            ):
                raise HwpLiveError(
                    "계층형 표 제목 문단의 스타일과 서식 원본 위치를 일치시켜 읽지 못했습니다"
                )
            return TableCaptionFormatSource(
                style_id=snapshot.style_id,
                position=native_selection.start,
            )
    return None


def find_hierarchical_table_caption_source(
    candidate: HwpDocumentCandidate,
    hwp: LiveHwpApplication,
    guard: Callable[[], None],
    *,
    setup_page: int,
) -> TableCaptionFormatSource | None:
    with preserved_live_state(hwp, guard=guard):
        for page in range(setup_page, 0, -1):
            guard()
            page_text = hwp.get_page_text(page - 1)[:200_000]
            guard()
            if not has_hierarchical_table_caption(page_text):
                continue
            detailed = inspect_native_structure(candidate.window_handle, page)
            guard()
            if detailed is None:
                raise HwpLiveError("계층형 표 캡션의 네이티브 구조를 읽을 수 없습니다")
            if not _is_candidate_document(
                candidate,
                detailed.document_id,
                detailed.full_name,
            ):
                raise HwpLiveError(
                    "계층형 표 캡션 구조 조회 결과가 대상 문서와 다릅니다"
                )
            control_order = {
                control.instance_id: control.anchor
                for control in detailed.controls
                if control.control_type == "tbl"
            }
            captions = sorted(
                (
                    caption
                    for caption in detailed.captions
                    if caption.style_id is not None
                ),
                key=lambda caption: (
                    (
                        position.list_id,
                        position.paragraph,
                        position.character,
                    )
                    if (position := control_order.get(caption.table_instance_id))
                    is not None
                    else (0, 0, 0)
                ),
                reverse=True,
            )
            for caption in captions:
                style_id = caption.style_id
                if style_id is None:
                    continue
                source = _read_attached_caption_source(
                    candidate,
                    caption.table_instance_id,
                    style_id,
                    hierarchy_confirmed=caption_text_uses_hierarchy(
                        page_text,
                        caption.text,
                    ),
                )
                guard()
                if source is not None:
                    return source
            source = _read_preceding_caption_source(
                candidate,
                hwp,
                guard,
                page=page,
                table_positions=tuple(control_order.values()),
            )
            if source is not None:
                return source
        return None
