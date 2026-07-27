from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from hwp_live_api import LiveHwpApplication
from hwp_live_native_action_models import NativePageInspection
from hwp_live_rot import HwpDocumentCandidate, HwpDocumentIdentity


RoutingContextReader = Callable[[int, int | None], NativePageInspection | None]


@dataclass(frozen=True, slots=True)
class LiveSessionReference:
    session_id: str
    selector: str


class DocumentCatalog(Protocol):
    def scan(self) -> tuple[HwpDocumentCandidate, ...]: ...

    def close(self) -> None: ...


@runtime_checkable
class IdentityDocumentCatalog(Protocol):
    def resolve_identity(
        self,
        identity: HwpDocumentIdentity,
    ) -> HwpDocumentCandidate: ...

    def release_com_references(self) -> None: ...


class WrapperAttacher(Protocol):
    def __call__(
        self,
        candidate: HwpDocumentCandidate,
        wrapper: LiveHwpApplication | None = None,
    ) -> LiveHwpApplication: ...


class WrapperReleaser(Protocol):
    def __call__(self, wrapper: LiveHwpApplication) -> None: ...
