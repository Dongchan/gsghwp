from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_bridge_mixin import HancomBridgeSessionRuntime
from hwp_live_contract import (
    ConnectedDocument,
    MutationResult,
    OpenDocument,
    OpenDocumentList,
)
from hwp_live_session_candidate import select_operation_document


class HancomBridgeSessionMixin(HancomBridgeSessionRuntime):
    __slots__: tuple[str, ...] = ()

    def _reuse_known_connection(
        self,
        selector: str | None,
    ) -> ConnectedDocument | None:
        known = self._known_connection(selector)
        if known is None:
            return None

        def operation() -> ConnectedDocument:
            controller = self._bridge_controller()
            existing = controller.session_for_selector(known.document.selector)
            current = controller.current_session() if existing is None else None
            exact_session = (
                existing is not None and existing.session_id == known.session_id
            ) or (
                current is not None
                and current.session_id == known.session_id
                and current.selector == known.document.selector
            )
            if not exact_session:
                raise HwpLiveError(
                    "".join(
                        (
                            "알려진 한컴 문서 세션을 프로세스별 연결에서 재사용할 수 없습니다",
                            f"; selector={known.document.selector}",
                            f"; window_handle={known.document.window_handle}",
                            "; known_connection_stale=true",
                            "; global_discovery_skipped=true",
                            "; reconnect_required=true",
                            "; mutation_started=false",
                            "; retry_safe=true",
                            "; user_action=저장 작업이면 먼저 hwp_get_operation_status로 ",
                            "파일 지문을 판정하세요. hwp_disconnect가 ",
                            "save_close_blocked=true를 반환하면 한글을 닫지 말고 ",
                            "문서 상태를 직접 확인하세요. guard가 없을 때만 stale 연결을 ",
                            "정리하고 hwp_list_open_documents에서 selector를 다시 선택하세요",
                        )
                    )
                )
            return known

        return self._call(operation, session_id=known.session_id)

    def list_open_documents(self) -> OpenDocumentList:
        return self._call(self._bridge_controller().list_open_documents)

    def open_document(
        self,
        path: str,
        reference_selector: str | None,
        new_tab: bool,
        restore_reference: bool = True,
    ) -> OpenDocument:
        reference = select_operation_document(
            self.list_open_documents(),
            reference_selector,
        )
        process_id = self._bridge_process_id(reference.window_handle)
        return self._call_mutation(
            lambda: self._bridge_controller().open_document(
                path,
                reference.selector,
                new_tab,
                restore_reference,
            ),
            process_id=process_id,
        )

    def _connect(
        self,
        selector: str,
        *,
        deferred: bool = False,
        persistent: bool | None = True,
    ) -> ConnectedDocument:
        controller = self._bridge_controller()
        connected = (
            controller.connect_deferred(selector)
            if deferred
            else controller.connect(selector)
        )
        try:
            self._activate_connection(connected)
        except (HwpLiveError, OSError, RuntimeError, ValueError):
            try:
                _ = controller.disconnect(connected.session_id)
            finally:
                self._clear_connection_state(connected.session_id)
            raise
        self._set_connection_lifetime(
            connected.session_id,
            persistent=persistent,
            created=True,
        )
        return connected

    def connect(self, selector: str) -> ConnectedDocument:
        selected = select_operation_document(
            self.list_open_documents(),
            selector,
        )
        process_id = self._bridge_process_id(selected.window_handle)
        return self._call(
            lambda: self._connect(selected.selector),
            process_id=process_id,
        )

    def ensure_connection(
        self,
        selector: str | None,
        *,
        persistent: bool | None = None,
    ) -> ConnectedDocument:
        known = self._reuse_known_connection(selector)
        if known is not None:
            self._set_connection_lifetime(
                known.session_id,
                persistent=persistent,
                created=False,
            )
            return known
        selected = select_operation_document(
            self.list_open_documents(),
            selector,
        )
        process_id = self._bridge_process_id(selected.window_handle)

        def operation() -> ConnectedDocument:
            controller = self._bridge_controller()
            existing = controller.session_for_selector(selected.selector)
            if existing is not None:
                connected = ConnectedDocument(
                    session_id=existing.session_id,
                    document=selected,
                )
                self._set_connection_lifetime(
                    connected.session_id,
                    persistent=persistent,
                    created=False,
                )
                return connected
            current = controller.current_session()
            if current is not None and current.selector == selected.selector:
                connected = ConnectedDocument(
                    session_id=current.session_id,
                    document=selected,
                )
                self._set_connection_lifetime(
                    connected.session_id,
                    persistent=persistent,
                    created=False,
                )
                return connected
            try:
                return self._connect(
                    selected.selector,
                    deferred=True,
                    persistent=persistent,
                )
            except HwpLiveError as error:
                if (
                    current is None
                    or "이미 한컴 라이브 문서에 연결" not in error.reason
                ):
                    raise
                try:
                    _ = controller.disconnect(current.session_id)
                finally:
                    if controller.current_session() is None:
                        self._clear_connection_state(current.session_id)
                return self._connect(
                    selected.selector,
                    deferred=True,
                    persistent=persistent,
                )

        return self._call(operation, process_id=process_id)

    def disconnect(self, session_id: str | None = None) -> MutationResult:
        def operation() -> MutationResult:
            controller = self._bridge_controller()
            current = controller.current_session()
            selected_session = (
                (None if current is None else current.session_id)
                if session_id is None
                else session_id
            )
            if selected_session is None:
                raise HwpLiveError("연결된 한컴 라이브 세션이 없습니다")
            try:
                return controller.disconnect(selected_session)
            finally:
                self._clear_connection_state(selected_session)

        return self._call(operation, session_id=session_id)
