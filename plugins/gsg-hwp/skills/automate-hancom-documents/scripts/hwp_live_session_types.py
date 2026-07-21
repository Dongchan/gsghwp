from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from hwp_live_api import LiveHwpApplication
from hwp_live_native_action_models import NativePageInspection
from hwp_live_rot import HwpDocumentCandidate


RoutingContextReader = Callable[[int, int | None], NativePageInspection | None]


class DocumentCatalog(Protocol):
    def scan(self) -> tuple[HwpDocumentCandidate, ...]: ...

    def close(self) -> None: ...


class WrapperAttacher(Protocol):
    def __call__(
        self,
        candidate: HwpDocumentCandidate,
        wrapper: LiveHwpApplication | None = None,
    ) -> LiveHwpApplication: ...


class WrapperReleaser(Protocol):
    def __call__(self, wrapper: LiveHwpApplication) -> None: ...
