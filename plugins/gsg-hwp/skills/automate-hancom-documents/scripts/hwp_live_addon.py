from __future__ import annotations

from typing import Protocol, final

from hwp_errors import HwpLiveError


ADDON_MONIKER_PREFIX = "!HancomLiveBridge."


class HwpAddOnBridge(Protocol):
    @property
    def ProtocolVersion(self) -> int: ...

    @property
    def DocumentID(self) -> int: ...

    @property
    def FullName(self) -> str: ...

    @property
    def Format(self) -> str: ...

    @property
    def EditMode(self) -> int: ...

    @property
    def Modified(self) -> int: ...

    @property
    def PageCount(self) -> int: ...

    @property
    def WindowHandle(self) -> int: ...

    @property
    def CurrentPage(self) -> int: ...

    @property
    def AppVersion(self) -> str: ...

    def Invoke(self, request_json: str) -> str: ...


@final
class AddOnDocumentInfo:
    __slots__ = ("_bridge",)

    def __init__(self, bridge: HwpAddOnBridge) -> None:
        self._bridge = bridge

    @property
    def CurrentPage(self) -> int:
        return self._bridge.CurrentPage


@final
class AddOnDocument:
    __slots__ = ("_bridge", "_info")

    def __init__(self, bridge: HwpAddOnBridge) -> None:
        self._bridge = bridge
        self._info = AddOnDocumentInfo(bridge)

    @property
    def DocumentID(self) -> int:
        return self._bridge.DocumentID

    @property
    def FullName(self) -> str:
        return self._bridge.FullName

    @property
    def Format(self) -> str:
        return self._bridge.Format

    @property
    def EditMode(self) -> int:
        return self._bridge.EditMode

    @property
    def Modified(self) -> int:
        return self._bridge.Modified

    @property
    def XHwpDocumentInfo(self) -> AddOnDocumentInfo:
        return self._info


@final
class AddOnDocuments:
    __slots__ = ("_document",)

    def __init__(self, bridge: HwpAddOnBridge) -> None:
        self._document = AddOnDocument(bridge)

    @property
    def Count(self) -> int:
        return 1

    @property
    def Active_XHwpDocument(self) -> AddOnDocument:
        return self._document

    def Item(self, index: int) -> AddOnDocument:
        if index != 0:
            raise HwpLiveError("인프로세스 브리지 문서 인덱스가 올바르지 않습니다")
        return self._document

    def FindItem(self, document_id: int) -> AddOnDocument | None:
        if document_id == self._document.DocumentID:
            return self._document
        return None


@final
class AddOnWindow:
    __slots__ = ("_bridge",)

    def __init__(self, bridge: HwpAddOnBridge) -> None:
        self._bridge = bridge

    @property
    def WindowHandle(self) -> int:
        return self._bridge.WindowHandle


@final
class AddOnWindows:
    __slots__ = ("_window",)

    def __init__(self, bridge: HwpAddOnBridge) -> None:
        self._window = AddOnWindow(bridge)

    @property
    def Active_XHwpWindow(self) -> AddOnWindow:
        return self._window


@final
class LiveAddOnApplication:
    __slots__ = ("_bridge", "_documents", "_windows")

    def __init__(self, bridge: HwpAddOnBridge) -> None:
        self._bridge = bridge
        self._documents = AddOnDocuments(bridge)
        self._windows = AddOnWindows(bridge)

    @property
    def XHwpDocuments(self) -> AddOnDocuments:
        return self._documents

    @property
    def XHwpWindows(self) -> AddOnWindows:
        return self._windows

    @property
    def PageCount(self) -> int:
        return self._bridge.PageCount

    def RegisterModule(self, *, ModuleType: str, ModuleData: str) -> bool:
        _ = (ModuleType, ModuleData)
        return True
