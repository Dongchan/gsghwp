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

    def list_open_documents(self) -> OpenDocumentList:
        return self._call(self._bridge_controller().list_open_documents)

    def open_document(
        self,
        path: str,
        reference_selector: str | None,
        new_tab: bool,
    ) -> OpenDocument:
        return self._call_mutation(
            lambda: self._bridge_controller().open_document(
                path,
                reference_selector,
                new_tab,
            )
        )

    def _connect(
        self,
        selector: str,
        *,
        deferred: bool = False,
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
        return connected

    def connect(self, selector: str) -> ConnectedDocument:
        return self._call(lambda: self._connect(selector))

    def ensure_connection(self, selector: str | None) -> ConnectedDocument:
        def operation() -> ConnectedDocument:
            controller = self._bridge_controller()
            listing = controller.list_open_documents()
            selected = select_operation_document(listing, selector)
            existing = controller.session_for_selector(selected.selector)
            if existing is not None:
                return ConnectedDocument(
                    session_id=existing.session_id,
                    document=selected,
                )
            current = controller.current_session()
            if current is not None and current.selector == selected.selector:
                return ConnectedDocument(
                    session_id=current.session_id,
                    document=selected,
                )
            try:
                return self._connect(selected.selector, deferred=True)
            except HwpLiveError as error:
                if current is None or "이미 한컴 라이브 문서에 연결" not in error.reason:
                    raise
                try:
                    _ = controller.disconnect(current.session_id)
                finally:
                    if controller.current_session() is None:
                        self._clear_connection_state(current.session_id)
                return self._connect(selected.selector, deferred=True)

        return self._call(operation)

    def disconnect(self, session_id: str | None = None) -> MutationResult:
        def operation() -> MutationResult:
            controller = self._bridge_controller()
            current = controller.current_session()
            selected_session = (
                None if current is None else current.session_id
            ) if session_id is None else session_id
            if selected_session is None:
                raise HwpLiveError("연결된 한컴 라이브 세션이 없습니다")
            try:
                return controller.disconnect(selected_session)
            finally:
                self._clear_connection_state(selected_session)

        return self._call(operation, session_id=session_id)
