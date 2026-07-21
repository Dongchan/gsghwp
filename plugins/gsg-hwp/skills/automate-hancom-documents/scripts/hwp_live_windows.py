from __future__ import annotations

import time
from collections.abc import Callable
from importlib import import_module
from typing import Protocol, final, runtime_checkable

from hwp_errors import HwpLiveError
from hwp_live_bridge_contract import (
    HancomDialogDismissResult,
    HancomDialogState,
    HancomWindowChildState,
    HancomWindowState,
    HancomWindowStateList,
)


WindowEnumerator = Callable[[int, int], bool]


@runtime_checkable
class Win32GuiModule(Protocol):
    def EnumChildWindows(
        self,
        window_handle: int,
        callback: WindowEnumerator,
        extra: int,
    ) -> None: ...

    def EnumWindows(self, callback: WindowEnumerator, extra: int) -> None: ...

    def GetClassName(self, window_handle: int) -> str: ...

    def GetForegroundWindow(self) -> int: ...

    def GetWindow(self, window_handle: int, command: int) -> int: ...

    def GetWindowText(self, window_handle: int) -> str: ...

    def IsWindow(self, window_handle: int) -> bool: ...

    def IsWindowEnabled(self, window_handle: int) -> bool: ...

    def IsWindowVisible(self, window_handle: int) -> bool: ...

    def PostMessage(
        self,
        window_handle: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> None: ...


@runtime_checkable
class Win32ProcessModule(Protocol):
    def GetWindowThreadProcessId(self, window_handle: int) -> tuple[int, int]: ...


@runtime_checkable
class PyWinTypesModule(Protocol):
    @property
    def error(self) -> type[Exception]: ...


class WindowStateReader(Protocol):
    def list_visible_hwp_windows(self) -> HancomWindowStateList: ...

    def read(self, window_handle: int) -> HancomWindowState: ...

    def dismiss_dialogs(self, window_handle: int) -> HancomDialogDismissResult: ...


def _load_gui() -> Win32GuiModule:
    module = import_module("win32gui")
    if not isinstance(module, Win32GuiModule):
        raise HwpLiveError("Windows 창 상태 API를 찾을 수 없습니다")
    return module


def _load_process() -> Win32ProcessModule:
    module = import_module("win32process")
    if not isinstance(module, Win32ProcessModule):
        raise HwpLiveError("Windows 프로세스 상태 API를 찾을 수 없습니다")
    return module


def _load_window_error() -> type[Exception]:
    module = import_module("pywintypes")
    if not isinstance(module, PyWinTypesModule):
        raise HwpLiveError("Windows 창 오류 형식을 찾을 수 없습니다")
    return module.error


_WINDOW_ERROR = _load_window_error()
_GW_OWNER = 4
_WM_CLOSE = 0x0010


@final
class Win32WindowStateReader:
    __slots__ = ("_gui", "_process")

    _gui: Win32GuiModule
    _process: Win32ProcessModule

    def __init__(self) -> None:
        self._gui = _load_gui()
        self._process = _load_process()

    def _dialog(
        self,
        window_handle: int,
        owner_handle: int,
        main_enabled: bool,
    ) -> HancomDialogState:
        return HancomDialogState(
            window_handle=window_handle,
            owner_handle=owner_handle,
            title=self._gui.GetWindowText(window_handle),
            class_name=self._gui.GetClassName(window_handle),
            visible=self._gui.IsWindowVisible(window_handle),
            enabled=self._gui.IsWindowEnabled(window_handle),
            modal=not main_enabled,
        )

    def _dialogs(
        self,
        main_handle: int,
        process_id: int,
        main_enabled: bool,
    ) -> tuple[HancomDialogState, ...]:
        dialogs: list[HancomDialogState] = []

        def collect(window_handle: int, extra: int) -> bool:
            _ = extra
            try:
                _, candidate_pid = self._process.GetWindowThreadProcessId(window_handle)
                if candidate_pid != process_id or window_handle == main_handle:
                    return True
                if not self._gui.IsWindowVisible(window_handle):
                    return True
                owner = self._gui.GetWindow(window_handle, _GW_OWNER)
                class_name = self._gui.GetClassName(window_handle)
                title = self._gui.GetWindowText(window_handle)
                orphan_wpf_dialog = (
                    not main_enabled
                    and owner == 0
                    and class_name.startswith("HwndWrapper[Hwp.exe;")
                    and not title.endswith(" - 한글")
                )
                if (
                    owner == 0
                    and class_name != "#32770"
                    and not orphan_wpf_dialog
                ):
                    return True
                dialogs.append(self._dialog(window_handle, owner, main_enabled))
            except _WINDOW_ERROR:
                return True
            return True

        self._gui.EnumWindows(collect, 0)
        return tuple(sorted(dialogs, key=lambda item: item.window_handle))

    def _children(self, window_handle: int) -> tuple[HancomWindowChildState, ...]:
        children: list[HancomWindowChildState] = []

        def collect(child_handle: int, extra: int) -> bool:
            _ = extra
            try:
                children.append(
                    HancomWindowChildState(
                        window_handle=child_handle,
                        title=self._gui.GetWindowText(child_handle),
                        class_name=self._gui.GetClassName(child_handle),
                        visible=self._gui.IsWindowVisible(child_handle),
                        enabled=self._gui.IsWindowEnabled(child_handle),
                    )
                )
            except _WINDOW_ERROR:
                return True
            return True

        self._gui.EnumChildWindows(window_handle, collect, 0)
        return tuple(sorted(children, key=lambda item: item.window_handle))

    def read(self, window_handle: int) -> HancomWindowState:
        if not self._gui.IsWindow(window_handle):
            return HancomWindowState(
                window_handle=window_handle,
                process_id=0,
                exists=False,
                visible=False,
                enabled=False,
                foreground=False,
                title="",
                class_name="",
                dialogs=(),
                children=(),
            )
        try:
            _, process_id = self._process.GetWindowThreadProcessId(window_handle)
            enabled = self._gui.IsWindowEnabled(window_handle)
            return HancomWindowState(
                window_handle=window_handle,
                process_id=process_id,
                exists=True,
                visible=self._gui.IsWindowVisible(window_handle),
                enabled=enabled,
                foreground=self._gui.GetForegroundWindow() == window_handle,
                title=self._gui.GetWindowText(window_handle),
                class_name=self._gui.GetClassName(window_handle),
                dialogs=self._dialogs(window_handle, process_id, enabled),
                children=self._children(window_handle),
            )
        except _WINDOW_ERROR as error:
            raise HwpLiveError("한컴 창과 대화상자 상태를 읽을 수 없습니다") from error

    def list_visible_hwp_windows(self) -> HancomWindowStateList:
        handles: list[int] = []

        def collect(window_handle: int, extra: int) -> bool:
            _ = extra
            try:
                if not self._gui.IsWindowVisible(window_handle):
                    return True
                class_name = self._gui.GetClassName(window_handle)
                title = self._gui.GetWindowText(window_handle)
                if (
                    "hwp" in class_name.casefold()
                    or title == "Hwp"
                    or "hwp" in title.casefold()
                    or title.endswith(" - 한글")
                ):
                    handles.append(window_handle)
            except _WINDOW_ERROR:
                return True
            return True

        self._gui.EnumWindows(collect, 0)
        return HancomWindowStateList(
            windows=tuple(self.read(handle) for handle in sorted(set(handles)))
        )

    def dismiss_dialogs(self, window_handle: int) -> HancomDialogDismissResult:
        before = self.read(window_handle)
        dismissed: list[int] = []
        state = before
        for _ in range(8):
            if not state.dialogs:
                break
            target = next(
                (dialog for dialog in state.dialogs if dialog.enabled),
                state.dialogs[0],
            )
            try:
                self._gui.PostMessage(target.window_handle, _WM_CLOSE, 0, 0)
            except _WINDOW_ERROR:
                break
            dismissed.append(target.window_handle)
            time.sleep(0.25)
            state = self.read(window_handle)
        return HancomDialogDismissResult(
            before=before,
            after=state,
            dismissed_handles=tuple(dismissed),
        )
