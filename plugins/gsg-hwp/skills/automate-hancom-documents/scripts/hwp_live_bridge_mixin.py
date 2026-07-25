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
    ) -> T:
        _ = operation
        _ = session_id
        _ = process_id
        raise NotImplementedError


class HancomBridgeSessionRuntime(HancomBridgeMutationRuntime):
    __slots__: tuple[str, ...] = ()

    def _call(
        self,
        operation: Callable[[], T],
        *,
        session_id: str | None = None,
        process_id: int | None = None,
        mutation: bool = False,
    ) -> T:
        _ = operation
        _ = session_id
        _ = process_id
        _ = mutation
        raise NotImplementedError

    def _activate_connection(self, connected: ConnectedDocument) -> None:
        _ = connected
        raise NotImplementedError

    def _clear_connection_state(self, session_id: str | None = None) -> None:
        _ = session_id
        raise NotImplementedError
