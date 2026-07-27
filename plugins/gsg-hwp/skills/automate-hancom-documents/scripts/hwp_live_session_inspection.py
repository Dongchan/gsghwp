from __future__ import annotations

import ntpath
from typing import cast

from hwp_errors import HwpLiveError
from hwp_live_contract import DocumentStyleList, LiveContext, PreviewResult
from hwp_live_inspection import inspect_styles
from hwp_live_native_batch import inspect_native_page, read_native_snapshot
from hwp_live_preview import PreviewApplication, render_page
from hwp_live_session_core import LiveHwpSessionCore
from hwp_live_session_structure import (
    inspect_candidate_context,
    inspect_candidate_state,
    inspect_candidate_structure,
)
from hwp_live_structure_contract import (
    DocumentStructure,
    FastControlInspectionError,
    FastPageCell,
    FastPageControl,
    FastPageInspection,
    FastParagraphFormat,
    StructurePosition,
)


def _hwpunit_to_mm(value: int | None) -> float | None:
    return None if value is None else round(value / 283.4645669, 3)


class LiveHwpInspectionSession(LiveHwpSessionCore):
    __slots__: tuple[str, ...] = ()
    _structure_snapshot: DocumentStructure | None

    def context(self, session_id: str) -> LiveContext:
        candidate, hwp = self._validate(session_id)
        return inspect_candidate_context(
            hwp,
            candidate,
            self._guard(candidate, hwp),
        )

    def styles(self, session_id: str) -> DocumentStyleList:
        candidate, hwp = self._validate(session_id)
        guard = self._guard(candidate, hwp)
        state_token = self.style_state_token(session_id)
        if state_token is None:
            return inspect_styles(hwp, guard)
        return self._style_cache.resolve(
            candidate.document_id,
            candidate.full_name,
            state_token,
            lambda: inspect_styles(hwp, guard),
        )

    def structure(self, session_id: str, page: int = 0) -> DocumentStructure:
        candidate, hwp = self._validate(session_id)
        self._structure_snapshot = inspect_candidate_structure(
            hwp,
            candidate,
            page,
            self._guard(candidate, hwp),
        )
        return self._structure_snapshot

    def inspect_page_fast(
        self,
        session_id: str,
        page: int = 0,
        *,
        include_cells: bool = False,
    ) -> FastPageInspection:
        candidate, hwp = self._validate(session_id)
        guard = self._guard(candidate, hwp)
        target_page = page
        if page == 0:
            snapshot = read_native_snapshot(candidate.window_handle)
            guard()
            if snapshot is None:
                raise HwpLiveError("한컴 네이티브 현재 쪽을 읽을 수 없습니다")
            expected_name = ntpath.normcase(ntpath.normpath(candidate.full_name))
            actual_name = ntpath.normcase(ntpath.normpath(snapshot.full_name))
            if (
                snapshot.document_id != candidate.document_id
                or actual_name != expected_name
            ):
                raise HwpLiveError(
                    "한컴 네이티브 현재 쪽 문서가 현재 연결 문서와 다릅니다"
                )
            target_page = snapshot.current_page
        guard()
        inspected = inspect_native_page(
            candidate.window_handle,
            target_page,
            include_cells=include_cells,
        )
        if inspected is None:
            raise HwpLiveError(
                "한컴 네이티브 인프로세스 구조 조회를 사용할 수 없습니다"
            )
        expected_name = ntpath.normcase(ntpath.normpath(candidate.full_name))
        actual_name = ntpath.normcase(ntpath.normpath(inspected.full_name))
        if (
            inspected.document_id != candidate.document_id
            or actual_name != expected_name
        ):
            raise HwpLiveError(
                "한컴 네이티브 구조 조회 문서가 현재 연결 문서와 다릅니다"
            )
        return FastPageInspection(
            document_id=inspected.document_id,
            full_name=inspected.full_name,
            page=inspected.page,
            page_count=inspected.page_count,
            text=inspected.text,
            controls=tuple(
                FastPageControl(
                    control_type=control.control_type,
                    instance_id=control.instance_id,
                    anchor=StructurePosition(
                        list_id=control.anchor.list_id,
                        paragraph=control.anchor.paragraph,
                        character=control.anchor.character,
                    ),
                    rows=control.rows,
                    columns=control.columns,
                    width_hwpunit=control.width_hwpunit,
                    height_hwpunit=control.height_hwpunit,
                    width_mm=_hwpunit_to_mm(control.width_hwpunit),
                    height_mm=_hwpunit_to_mm(control.height_hwpunit),
                    anchor_style_id=control.anchor_style_id,
                    anchor_paragraph_format=None
                    if control.anchor_paragraph_format is None
                    else FastParagraphFormat(
                        alignment=control.anchor_paragraph_format.alignment,
                        line_spacing=control.anchor_paragraph_format.line_spacing,
                        left_margin_hwpunit=control.anchor_paragraph_format.left_margin_hwpunit,
                        right_margin_hwpunit=control.anchor_paragraph_format.right_margin_hwpunit,
                        indentation_hwpunit=control.anchor_paragraph_format.indentation_hwpunit,
                        previous_spacing_hwpunit=control.anchor_paragraph_format.previous_spacing_hwpunit,
                        next_spacing_hwpunit=control.anchor_paragraph_format.next_spacing_hwpunit,
                    ),
                )
                for control in inspected.controls
            ),
            cells=tuple(
                FastPageCell(
                    table_instance_id=cell.table_instance_id,
                    address=cell.address,
                    list_id=cell.list_id,
                    row_span=cell.row_span,
                    column_span=cell.column_span,
                    text=cell.text,
                    width_hwpunit=cell.width_hwpunit,
                    height_hwpunit=cell.height_hwpunit,
                    width_mm=_hwpunit_to_mm(cell.width_hwpunit),
                    height_mm=_hwpunit_to_mm(cell.height_hwpunit),
                )
                for cell in inspected.cells
            ),
            inspection_errors=tuple(
                FastControlInspectionError(
                    control_instance_id=error.control_instance_id,
                    code=error.code,
                    message=error.message,
                )
                for error in inspected.inspection_errors
            ),
        )

    def inspect_state(
        self,
        session_id: str,
        page: int = 0,
    ) -> tuple[LiveContext, DocumentStructure, int]:
        candidate, hwp = self._validate(session_id)
        return inspect_candidate_state(
            hwp,
            candidate,
            page,
            self._guard(candidate, hwp),
        )

    def render_page(
        self,
        session_id: str,
        page: int,
        dpi: int,
    ) -> PreviewResult:
        candidate, hwp = self._validate(session_id)
        preview_session = self._sessions[session_id].preview_session
        result = render_page(
            candidate,
            cast(PreviewApplication, cast(object, hwp)),
            page,
            dpi,
            session_id,
            self._guard(candidate, hwp),
            directory=preview_session.directory,
        )
        preview_session.commit(result.path)
        return result
