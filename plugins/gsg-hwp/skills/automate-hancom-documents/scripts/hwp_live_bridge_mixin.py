from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from hwp_live_session import LiveHwpController


T = TypeVar("T")


class HancomBridgeMutationRuntime:
    __slots__: tuple[str, ...] = ()

    def _bridge_controller(self) -> LiveHwpController:
        raise NotImplementedError

    def _call_mutation(self, operation: Callable[[], T]) -> T:
        _ = operation
        raise NotImplementedError
