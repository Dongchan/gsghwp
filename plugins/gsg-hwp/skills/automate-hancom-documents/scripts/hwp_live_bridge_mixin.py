from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from hwp_live_contract import ConnectedDocument
from hwp_live_session import LiveHwpController


T = TypeVar("T")


class HancomBridgeMutationRuntime:
    __slots__: tuple[str, ...] = ()

    def _bridge_controller(self) -> LiveHwpController:
        raise NotImplementedError

    def _bridge_process_id(self, window_handle: int) -> int:
        _ = window_handle
        raise NotImplementedError

    def _call_mutation(
        self,
        operation: Callable[[], T],
        *,
        session_id: str | None = None,
        process_id: int | None = None,
        save_operation: bool = False,
    ) -> T:
        _ = operation
        _ = session_id
        _ = process_id
        _ = save_operation
        raise NotImplementedError


class HancomBridgeSessionRuntime(HancomBridgeMutationRuntime):
    __slots__: tuple[str, ...] = ()

    def _set_connection_lifetime(
        self,
        session_id: str,
        *,
        persistent: bool | None,
        created: bool,
    ) -> None:
        _ = session_id
        _ = persistent
        _ = created
        raise NotImplementedError

    def _known_connection(self, selector: str | None) -> ConnectedDocument | None:
        _ = selector
        raise NotImplementedError

    def _call_style_read(
        self,
        operation: Callable[[LiveHwpController], T],
        *,
        session_id: str,
    ) -> T:
        _ = operation
        _ = session_id
        raise NotImplementedError

    def _style_state_token(self, session_id: str) -> str:
        _ = session_id
        raise NotImplementedError

    def _call(
        self,
        operation: Callable[[], T],
        *,
        session_id: str | None = None,
        process_id: int | None = None,
        mutation: bool = False,
        save_operation: bool = False,
    ) -> T:
        _ = operation
        _ = session_id
        _ = process_id
        _ = mutation
        _ = save_operation
        raise NotImplementedError

    def _activate_connection(self, connected: ConnectedDocument) -> None:
        _ = connected
        raise NotImplementedError

    def _clear_connection_state(self, session_id: str | None = None) -> None:
        _ = session_id
        raise NotImplementedError
