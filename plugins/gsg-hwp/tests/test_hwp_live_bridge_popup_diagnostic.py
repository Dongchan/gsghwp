from __future__ import annotations

import sys
from pathlib import Path
from typing import final


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_bridge_contract import (  # noqa: E402
    HancomDialogDismissResult,
    HancomDialogState,
    HancomWindowChildState,
    HancomWindowState,
    HancomWindowStateList,
)
from hwp_live_bridge_diagnostic import popup_diagnostic  # noqa: E402


@final
class _WindowReader:
    def __init__(self, *windows: HancomWindowState) -> None:
        self.list_calls = 0
        self._windows = windows

    def list_visible_hwp_windows(self) -> HancomWindowStateList:
        self.list_calls += 1
        return HancomWindowStateList(windows=self._windows)

    def read(self, window_handle: int) -> HancomWindowState:
        return next(
            window for window in self._windows if window.window_handle == window_handle
        )

    def dismiss_dialogs(self, window_handle: int) -> HancomDialogDismissResult:
        _ = window_handle
        raise AssertionError("popup diagnostics must not dismiss dialogs")


def _window(
    *,
    window_handle: int,
    process_id: int,
    class_name: str,
    title: str,
    enabled: bool = True,
    dialogs: tuple[HancomDialogState, ...] = (),
    children: tuple[HancomWindowChildState, ...] = (),
) -> HancomWindowState:
    return HancomWindowState(
        window_handle=window_handle,
        process_id=process_id,
        exists=True,
        visible=True,
        enabled=enabled,
        foreground=False,
        title=title,
        class_name=class_name,
        dialogs=dialogs,
        children=children,
    )


def _dialog(
    *,
    window_handle: int,
    owner_handle: int,
    title: str,
    class_name: str = "#32770",
    modal: bool,
) -> HancomDialogState:
    return HancomDialogState(
        window_handle=window_handle,
        owner_handle=owner_handle,
        title=title,
        class_name=class_name,
        visible=True,
        enabled=True,
        modal=modal,
    )


def test_popup_diagnostic_excludes_other_hwp_process() -> None:
    other_process_error = _dialog(
        window_handle=201,
        owner_handle=200,
        title="other-process error",
        modal=True,
    )
    reader = _WindowReader(
        _window(
            window_handle=200,
            process_id=22,
            class_name="HwpMainFrame",
            title="other.hwp - 한글",
            enabled=False,
            dialogs=(other_process_error,),
        )
    )

    diagnostic = popup_diagnostic(reader, target_process_id=11)

    assert diagnostic is None


def test_popup_diagnostic_ignores_normal_hwp_main_window_with_modeless_dialog() -> None:
    normal_auxiliary_window = _dialog(
        window_handle=101,
        owner_handle=100,
        title="찾기",
        modal=False,
    )
    reader = _WindowReader(
        _window(
            window_handle=100,
            process_id=11,
            class_name="HwpMainFrame",
            title="normal.hwp - 한글",
            dialogs=(normal_auxiliary_window,),
        )
    )

    diagnostic = popup_diagnostic(reader, target_process_id=11)

    assert diagnostic is None


def test_popup_diagnostic_includes_target_process_error_window() -> None:
    error_dialog = _dialog(
        window_handle=101,
        owner_handle=100,
        title="target-process error",
        modal=True,
    )
    reader = _WindowReader(
        _window(
            window_handle=100,
            process_id=11,
            class_name="HwpMainFrame",
            title="target.hwp - 한글",
            enabled=False,
            dialogs=(error_dialog,),
        )
    )

    diagnostic = popup_diagnostic(reader, target_process_id=11)

    assert diagnostic is not None
    assert "popup_ownership=target_process" in diagnostic
    assert "ownership_note=대상 프로세스 소유 확인" in diagnostic
    assert "HWND=101" in diagnostic
    assert "owner_HWND=100" in diagnostic
    assert "target-process error" in diagnostic


def test_popup_diagnostic_excludes_non_hwp_process_window() -> None:
    explorer_dialog = HancomDialogState(
        window_handle=301,
        owner_handle=300,
        title="Open",
        class_name="#32770",
        visible=True,
        enabled=True,
        modal=True,
    )
    reader = _WindowReader(
        _window(
            window_handle=300,
            process_id=33,
            class_name="CabinetWClass",
            title="test.hwp - File Explorer",
            dialogs=(explorer_dialog,),
        )
    )

    diagnostic = popup_diagnostic(reader, target_process_id=11)

    assert diagnostic is None


def test_popup_diagnostic_marks_candidate_unverified_without_target_process() -> None:
    error_dialog = _dialog(
        window_handle=101,
        owner_handle=100,
        title="Hwp unscoped error",
        modal=True,
    )
    reader = _WindowReader(
        _window(
            window_handle=100,
            process_id=11,
            class_name="HwpMainFrame",
            title="unscoped.hwp - 한글",
            enabled=False,
            dialogs=(error_dialog,),
        )
    )

    diagnostic = popup_diagnostic(reader, target_process_id=0)

    assert diagnostic is not None
    assert "popup_ownership=unverified" in diagnostic
    assert (
        "ownership_note=대상 프로세스 소유 및 한컴 관련성 미확인 외부 창 후보"
        in diagnostic
    )
    assert "PID=11" in diagnostic
    assert "Hwp unscoped error" in diagnostic
    assert reader.list_calls == 1


def test_popup_diagnostic_excludes_standalone_unverified_dialog() -> None:
    reader = _WindowReader(
        _window(
            window_handle=100,
            process_id=33,
            class_name="#32770",
            title="Hwp-looking dialog from another process",
        )
    )

    diagnostic = popup_diagnostic(reader, target_process_id=0)

    assert diagnostic is None
    assert reader.list_calls == 1


def test_popup_diagnostic_includes_unverified_top_level_hwp_dialog_text() -> None:
    reader = _WindowReader(
        _window(
            window_handle=100,
            process_id=33,
            class_name="#32770",
            title="Hwp",
            children=(
                HancomWindowChildState(
                    window_handle=101,
                    title="Unexpected initialization failure",
                    class_name="Static",
                    visible=True,
                    enabled=True,
                ),
            ),
        )
    )

    diagnostic = popup_diagnostic(reader, target_process_id=0)

    assert diagnostic is not None
    assert "popup_ownership=unverified" in diagnostic
    assert "HWND=100" in diagnostic
    assert "class=#32770" in diagnostic
    assert "Unexpected initialization failure" in diagnostic
    assert reader.list_calls == 1


def test_popup_diagnostic_excludes_explorer_without_target_process() -> None:
    explorer_dialog = HancomDialogState(
        window_handle=301,
        owner_handle=300,
        title="Open",
        class_name="#32770",
        visible=True,
        enabled=True,
        modal=True,
    )
    reader = _WindowReader(
        _window(
            window_handle=300,
            process_id=33,
            class_name="CabinetWClass",
            title="test.hwp - File Explorer",
            dialogs=(explorer_dialog,),
        )
    )

    diagnostic = popup_diagnostic(reader, target_process_id=0)

    assert diagnostic is None
    assert reader.list_calls == 1
