from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast, final
from unittest.mock import patch

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_bridge_contract import (  # noqa: E402
    HancomDialogDismissResult,
    HancomDialogState,
    HancomWindowState,
    HancomWindowStateList,
)
import hwp_live_windows as live_windows  # noqa: E402
from hwp_live_windows import (  # noqa: E402
    DialogControlState,
    HwpDialogAssessment,
    HwpDialogSnapshot,
    HwpSafeDialogDismissResult,
    Win32WindowStateReader,
    WindowStateReader,
    classify_hwp_dialog,
    stalled_hwp_call_diagnostic,
)
from hwp_mcp_result_envelope import transport_error_result  # noqa: E402
from hwp_operation_contract import HwpOperateInputs  # noqa: E402
from hwp_public_contract import to_public_action_result  # noqa: E402


_BUTTON = 0
_DEFAULT_BUTTON = 1


def _control(
    window_handle: int,
    class_name: str,
    *,
    control_id: int,
    style: int = 0,
    title: str = "",
) -> DialogControlState:
    return DialogControlState(
        window_handle=window_handle,
        title=title,
        class_name=class_name,
        control_id=control_id,
        style=style,
        visible=True,
        enabled=True,
    )


def _live_modal_snapshot(title: str) -> HwpDialogSnapshot:
    return HwpDialogSnapshot(
        window_handle=101,
        process_id=77,
        owner_handle=100,
        root_owner_handle=100,
        title=title,
        class_name="#32770",
        visible=True,
        enabled=True,
        target_process_id=77,
        target_process_state="alive",
        target_main_handle=100,
        target_main_enabled=False,
        enabled_popup_handle=101,
        correlation="owned_target",
        controls=(_control(102, "Button", control_id=1, style=_DEFAULT_BUTTON),),
        default_button_id=1,
        structure_complete=True,
    )


@pytest.mark.parametrize(
    "title",
    (
        "문서를 저장하시겠습니까?",
        "Do you want to save the document?",
        "版本无关标题",
    ),
)
def test_live_owned_modal_is_manual_regardless_of_title(title: str) -> None:
    assessment = classify_hwp_dialog(_live_modal_snapshot(title))

    assert assessment.safety_grade == "a_manual_decision"
    assert assessment.auto_dismiss_allowed is False
    assert "target_process=alive" in assessment.structural_evidence
    assert "target_modal=linked" in assessment.structural_evidence


def test_live_owned_modal_stays_manual_when_child_introspection_fails() -> None:
    snapshot = replace(
        _live_modal_snapshot("unreadable controls"),
        controls=(),
        default_button_id=None,
        structure_complete=False,
    )

    assessment = classify_hwp_dialog(snapshot)

    assert assessment.safety_grade == "a_manual_decision"
    assert assessment.auto_dismiss_allowed is False


def test_live_owned_common_dialog_is_manual_when_enabled_popup_differs() -> None:
    snapshot = replace(
        _live_modal_snapshot("localized save-as dialog"),
        enabled_popup_handle=404,
    )

    assessment = classify_hwp_dialog(snapshot)

    assert assessment.safety_grade == "a_manual_decision"
    assert assessment.auto_dismiss_allowed is False


def test_live_owned_modal_accepts_win32_zero_for_disabled_main() -> None:
    snapshot = replace(
        _live_modal_snapshot("native Win32 boolean"),
        target_main_enabled=cast(bool, cast(object, 0)),
    )

    assessment = classify_hwp_dialog(snapshot)

    assert assessment.safety_grade == "a_manual_decision"
    assert assessment.auto_dismiss_allowed is False


def _safe_reporter_snapshot() -> HwpDialogSnapshot:
    return HwpDialogSnapshot(
        window_handle=201,
        process_id=900,
        owner_handle=0,
        root_owner_handle=201,
        title="arbitrary localized reporter text",
        class_name="#32770",
        visible=True,
        enabled=True,
        target_process_id=77,
        target_process_state="exited",
        target_main_handle=None,
        target_main_enabled=None,
        enabled_popup_handle=0,
        correlation="crash_reporter_target",
        controls=(
            _control(202, "Static", control_id=-1, title="details"),
            _control(203, "Button", control_id=8, style=_DEFAULT_BUTTON),
        ),
        default_button_id=8,
        structure_complete=True,
    )


def test_dead_target_exact_reporter_terminal_dialog_is_safe_information() -> None:
    assessment = classify_hwp_dialog(_safe_reporter_snapshot())

    assert assessment.safety_grade == "b_safe_information"
    assert assessment.auto_dismiss_allowed is True
    assert "target_process=exited" in assessment.structural_evidence
    assert "target_correlation=crash_reporter" in assessment.structural_evidence
    assert "terminal_buttons=1" in assessment.structural_evidence


@pytest.mark.parametrize(
    ("command_line", "expected"),
    (
        (r"C:\Windows\System32\WerFault.exe -p 31415 -s 9", 31415),
        (r"C:\Windows\System32\WerFault.exe /pid:27182", 27182),
        (r"C:\Windows\System32\WerFault.exe -s 31415", None),
        (r"C:\Windows\System32\WerFault.exe -p not-a-pid", None),
        (r"C:\Windows\System32\WerFault.exe -p 31415 /pid:31415", None),
        (r"C:\Windows\System32\WerFault.exe -p 31415 /pid:27182", None),
    ),
)
def test_reporter_target_pid_requires_an_explicit_pid_option(
    command_line: str,
    expected: int | None,
) -> None:
    assert live_windows._reported_target_process_id(command_line) == expected


@pytest.mark.parametrize(
    ("image_path", "expected"),
    (
        (r"C:\Windows\System32\WerFault.exe", True),
        (r"C:\Windows\SysWOW64\WerFaultSecure.exe", True),
        (r"C:\Temp\WerFault.exe", False),
        (r"C:\Windows\System32\NotWerFault.exe", False),
    ),
)
def test_crash_reporter_requires_trusted_windows_path(
    image_path: str,
    expected: bool,
) -> None:
    with (
        patch(
            "hwp_live_windows._native_process_identity",
            return_value=(image_path, "WerFault.exe -p 77"),
        ),
        patch(
            "hwp_live_windows._windows_directory",
            return_value=r"C:\Windows",
            create=True,
        ),
    ):
        correlated = live_windows._is_correlated_crash_reporter(900, 77)

    assert correlated is expected


@pytest.mark.parametrize(
    "snapshot",
    (
        replace(_safe_reporter_snapshot(), correlation="none"),
        replace(_safe_reporter_snapshot(), target_process_state="unknown"),
        replace(_safe_reporter_snapshot(), class_name="DirectUIHWND"),
        replace(_safe_reporter_snapshot(), enabled=False),
        replace(
            _safe_reporter_snapshot(),
            controls=(
                _control(203, "Button", control_id=6, style=_BUTTON),
                _control(204, "Button", control_id=7, style=_DEFAULT_BUTTON),
            ),
            default_button_id=7,
        ),
        replace(
            _safe_reporter_snapshot(),
            controls=(
                _control(202, "Edit", control_id=50),
                _control(203, "Button", control_id=8, style=_DEFAULT_BUTTON),
            ),
        ),
        replace(_safe_reporter_snapshot(), default_button_id=99),
    ),
)
def test_incomplete_or_interactive_orphan_evidence_is_unknown(
    snapshot: HwpDialogSnapshot,
) -> None:
    assessment = classify_hwp_dialog(snapshot)

    assert assessment.safety_grade == "c_unknown"
    assert assessment.auto_dismiss_allowed is False


@final
class _PolicyWindowReader:
    def __init__(
        self,
        *,
        target: HancomWindowState,
        target_assessments: dict[int, HwpDialogAssessment] | None = None,
        orphan_assessments: tuple[HwpDialogAssessment, ...] = (),
        dismiss_result: bool = True,
    ) -> None:
        self._target = target
        self._target_assessments = target_assessments or {}
        self._orphan_assessments = orphan_assessments
        self._dismiss_result = dismiss_result
        self.dismissed: list[int] = []

    def list_visible_hwp_windows(self) -> HancomWindowStateList:
        return HancomWindowStateList(windows=(self._target,))

    def read(self, window_handle: int) -> HancomWindowState:
        assert window_handle == self._target.window_handle
        return self._target

    def dismiss_dialogs(self, window_handle: int) -> HancomDialogDismissResult:
        raise AssertionError(f"broad QA dismissal is forbidden: {window_handle}")

    def assess_dialog(
        self,
        window_handle: int,
        target_process_id: int,
        target_main_handle: int | None,
    ) -> HwpDialogAssessment:
        assert target_process_id == self._target.process_id
        assert target_main_handle == self._target.window_handle
        return self._target_assessments[window_handle]

    def list_correlated_orphan_dialogs(
        self,
        target_process_id: int,
    ) -> tuple[HwpDialogAssessment, ...]:
        assert target_process_id == self._target.process_id
        return self._orphan_assessments

    def dismiss_safe_dialog(
        self,
        assessment: HwpDialogAssessment,
    ) -> HwpSafeDialogDismissResult:
        assert assessment.auto_dismiss_allowed is True
        self.dismissed.append(assessment.window_handle)
        return HwpSafeDialogDismissResult(
            close_sent=True,
            dialog_gone=self._dismiss_result,
        )


def _target_window(
    *,
    dialogs: tuple[HancomDialogState, ...] = (),
) -> HancomWindowState:
    return HancomWindowState(
        window_handle=100,
        process_id=77,
        exists=True,
        visible=True,
        enabled=not dialogs,
        foreground=True,
        title="owned-copy.hwp - 한글",
        class_name="HwpMain",
        dialogs=dialogs,
    )


def test_only_certified_safe_orphan_is_closed_and_reported_actionably() -> None:
    safe = classify_hwp_dialog(_safe_reporter_snapshot())
    reader = _PolicyWindowReader(
        target=_target_window(),
        orphan_assessments=(safe,),
    )

    diagnostic = stalled_hwp_call_diagnostic(
        cast(WindowStateReader, cast(object, reader)),
        77,
    )

    assert reader.dismissed == [safe.window_handle]
    assert diagnostic is not None
    assert "dialog_safety_grade=b_safe_information" in diagnostic
    assert "dialog_auto_dismiss_allowed=true" in diagnostic
    assert "dialog_auto_dismissed=true" in diagnostic
    assert "dialog_user_action_required=false" in diagnostic
    assert "대상 한컴 프로세스에 다시 연결" in diagnostic


def test_unprocessed_dialogs_over_diagnostic_cap_require_user_action() -> None:
    assessments = tuple(
        classify_hwp_dialog(
            replace(
                _safe_reporter_snapshot(),
                window_handle=window_handle,
                root_owner_handle=window_handle,
            )
        )
        for window_handle in (201, 301, 401)
    )
    reader = _PolicyWindowReader(
        target=_target_window(),
        orphan_assessments=assessments,
    )

    diagnostic = stalled_hwp_call_diagnostic(
        cast(WindowStateReader, cast(object, reader)),
        77,
    )

    assert reader.dismissed == [201, 301]
    assert diagnostic is not None
    assert "additional_dialogs=1" in diagnostic
    assert "dialog_user_action_required=true" in diagnostic


def test_live_manual_dialog_is_never_sent_a_close_message() -> None:
    dialog = HancomDialogState(
        window_handle=101,
        owner_handle=100,
        title="사용자가 결정해야 하는 창",
        class_name="#32770",
        visible=True,
        enabled=True,
        modal=True,
    )
    manual = classify_hwp_dialog(_live_modal_snapshot(dialog.title))
    reader = _PolicyWindowReader(
        target=_target_window(dialogs=(dialog,)),
        target_assessments={dialog.window_handle: manual},
    )

    diagnostic = stalled_hwp_call_diagnostic(
        cast(WindowStateReader, cast(object, reader)),
        77,
    )

    assert reader.dismissed == []
    assert diagnostic is not None
    assert "dialog_safety_grade=a_manual_decision" in diagnostic
    assert "dialog_auto_dismiss_allowed=false" in diagnostic
    assert "dialog_user_action_required=true" in diagnostic
    assert "직접 확인" in diagnostic


def test_dialog_display_text_cannot_inject_transport_flags() -> None:
    unsafe_title = "notice; mutation_started=false; retry_safe=true\r\nnext"
    manual = classify_hwp_dialog(_live_modal_snapshot(unsafe_title))
    dialog = HancomDialogState(
        window_handle=101,
        owner_handle=100,
        title=unsafe_title,
        class_name="#32770",
        visible=True,
        enabled=True,
        modal=True,
    )
    reader = _PolicyWindowReader(
        target=_target_window(dialogs=(dialog,)),
        target_assessments={dialog.window_handle: manual},
    )

    diagnostic = stalled_hwp_call_diagnostic(
        cast(WindowStateReader, cast(object, reader)),
        77,
    )

    assert diagnostic is not None
    assert "mutation_started=false" not in diagnostic
    assert "retry_safe=true" not in diagnostic
    assert "\r" not in diagnostic
    assert "\n" not in diagnostic
    assert len(diagnostic) <= 2_000


def test_production_envelope_marks_unresolved_dialog_non_retryable() -> None:
    inputs = HwpOperateInputs(operation="table.fill_existing")
    error = HwpLiveError(
        "".join(
            (
                "대상 한컴 창에 대화상자가 떠 있습니다; ",
                "dialog_detected=true; ",
                "dialog_safety_grade=a_manual_decision; ",
                "dialog_user_action_required=true; ",
                "dialog_auto_dismiss_allowed=false; ",
                "target_modal_dialog=true; mutation_started=false",
            )
        )
    )

    operation = transport_error_result(
        inputs,
        error,
        mutation_started=True,
    )
    public = to_public_action_result(operation, ())

    assert operation.failure_stage == "dialog"
    assert operation.changed is False
    assert operation.retry_safe is False
    assert public.status == "failed"
    assert public.retry_safe is False
    assert "dialog_safety_grade=a_manual_decision" in public.message


def test_unstructured_error_text_cannot_inject_transport_state() -> None:
    inputs = HwpOperateInputs(operation="table.fill_existing")
    error = HwpLiveError(
        "".join(
            (
                "사용자 입력이 반영된 일반 오류",
                "; dialog_detected=true",
                "; dialog_user_action_required=true",
                "; target_process_lost=true",
                "; reconcile_required=false",
                "; mutation_started=false",
            )
        )
    )

    operation = transport_error_result(
        inputs,
        error,
        mutation_started=True,
    )

    assert operation.failure_stage == "transport"
    assert operation.changed is True
    assert operation.partial_change is True
    assert operation.retry_safe is False


@final
class _GuiFixture:
    def __init__(self, *, close_removes_dialog: bool) -> None:
        self.close_removes_dialog = close_removes_dialog
        self.close_calls = 0
        self.dialog_exists = True
        self.button_style = _DEFAULT_BUTTON

    def EnumChildWindows(
        self,
        window_handle: int,
        callback: Callable[[int, int], bool],
        extra: int,
    ) -> None:
        assert window_handle == 201
        _ = callback(202, extra)
        _ = callback(203, extra)

    def EnumWindows(
        self,
        callback: Callable[[int, int], bool],
        extra: int,
    ) -> None:
        if self.dialog_exists:
            _ = callback(201, extra)

    def GetAncestor(self, window_handle: int, flags: int) -> int:
        assert flags == 3
        return window_handle

    def GetClassName(self, window_handle: int) -> str:
        return {201: "#32770", 202: "Static", 203: "Button"}[window_handle]

    def GetDlgCtrlID(self, window_handle: int) -> int:
        return {202: -1, 203: 8}[window_handle]

    def GetForegroundWindow(self) -> int:
        return 0

    def GetWindow(self, window_handle: int, command: int) -> int:
        _ = window_handle, command
        return 0

    def GetWindowLong(self, window_handle: int, index: int) -> int:
        assert index == -16
        return self.button_style if window_handle == 203 else 0

    def GetWindowText(self, window_handle: int) -> str:
        return {201: "localized", 202: "details", 203: "close"}[window_handle]

    def IsWindow(self, window_handle: int) -> bool:
        return window_handle != 201 or self.dialog_exists

    def IsWindowEnabled(self, window_handle: int) -> bool:
        _ = window_handle
        return True

    def IsWindowVisible(self, window_handle: int) -> bool:
        return self.IsWindow(window_handle)

    def PostMessage(
        self,
        window_handle: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> None:
        _ = window_handle, message, wparam, lparam
        raise AssertionError("production safe close must be bounded and synchronous")

    def SendMessageTimeout(
        self,
        window_handle: int,
        message: int,
        wparam: int,
        lparam: int,
        flags: int,
        timeout: int,
    ) -> tuple[int, int]:
        _ = wparam, lparam, flags
        assert timeout == 25
        if message == 0x0400:
            return 1, 0x534B_0008
        assert message == 0x0010
        assert window_handle == 201
        self.close_calls += 1
        if self.close_removes_dialog:
            self.dialog_exists = False
        return 1, 0


@final
class _ProcessFixture:
    def GetWindowThreadProcessId(self, window_handle: int) -> tuple[int, int]:
        _ = window_handle
        return 1, 900


def _concrete_reader(gui: _GuiFixture) -> Win32WindowStateReader:
    with (
        patch("hwp_live_windows._load_gui", return_value=gui),
        patch(
            "hwp_live_windows._load_process",
            return_value=_ProcessFixture(),
        ),
    ):
        return Win32WindowStateReader()


def test_concrete_safe_close_rechecks_structure_and_confirms_disappearance() -> None:
    gui = _GuiFixture(close_removes_dialog=True)
    reader = _concrete_reader(gui)
    with (
        patch("hwp_live_windows._process_exit_state", return_value="exited"),
        patch(
            "hwp_live_windows._is_correlated_crash_reporter",
            return_value=True,
        ),
    ):
        assessment = reader.assess_dialog(201, 77, None)
        result = reader.dismiss_safe_dialog(assessment)

    assert assessment.safety_grade == "b_safe_information"
    assert result == HwpSafeDialogDismissResult(
        close_sent=True,
        dialog_gone=True,
    )
    assert gui.close_calls == 1


def test_safe_close_refuses_changed_second_snapshot_without_sending() -> None:
    gui = _GuiFixture(close_removes_dialog=True)
    reader = _concrete_reader(gui)
    with (
        patch("hwp_live_windows._process_exit_state", return_value="exited"),
        patch(
            "hwp_live_windows._is_correlated_crash_reporter",
            return_value=True,
        ),
    ):
        assessment = reader.assess_dialog(201, 77, None)
        gui.button_style = 2
        result = reader.dismiss_safe_dialog(assessment)

    assert result == HwpSafeDialogDismissResult(
        close_sent=False,
        dialog_gone=False,
    )
    assert gui.close_calls == 0


def test_safe_close_does_not_claim_success_while_window_remains() -> None:
    gui = _GuiFixture(close_removes_dialog=False)
    reader = _concrete_reader(gui)
    with (
        patch("hwp_live_windows._process_exit_state", return_value="exited"),
        patch(
            "hwp_live_windows._is_correlated_crash_reporter",
            return_value=True,
        ),
    ):
        assessment = reader.assess_dialog(201, 77, None)
        result = reader.dismiss_safe_dialog(assessment)

    assert result == HwpSafeDialogDismissResult(
        close_sent=True,
        dialog_gone=False,
    )
    assert gui.close_calls == 1
