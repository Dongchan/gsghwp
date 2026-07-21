from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_document_rebuild as rebuild  # noqa: E402
from hwp_errors import DocumentAutomationError  # noqa: E402


class _FakeWindow:
    WindowHandle = 1


class _FakeWindows:
    Active_XHwpWindow = _FakeWindow()


class _FakeApplication:
    XHwpWindows = _FakeWindows()

    def __init__(self, *, quit_error: Exception | None = None) -> None:
        self.message_modes: list[int] = []
        self.quit_calls = 0
        self._quit_error = quit_error

    def SetMessageBoxMode(self, mode: int) -> int:
        self.message_modes.append(mode)
        return 0

    def Quit(self) -> None:
        self.quit_calls += 1
        if self._quit_error is not None:
            raise self._quit_error


def test_close_application_still_quits_when_process_capture_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _FakeApplication()

    def fail_capture(
        _application: rebuild.CloseableApplication,
    ) -> rebuild._OwnedProcess:
        raise DocumentAutomationError("capture failed")

    monkeypatch.setattr(rebuild, "_capture_owned_process", fail_capture)

    with pytest.raises(DocumentAutomationError, match="capture failed"):
        rebuild._close_application(application)

    assert application.quit_calls == 1
    assert application.message_modes == [rebuild._AUTO_YES]


def test_close_application_forces_process_check_even_when_quit_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _FakeApplication(quit_error=RuntimeError("quit failed"))
    process = rebuild._OwnedProcess(91, 1234)
    checked: list[rebuild._OwnedProcess] = []
    monkeypatch.setattr(rebuild, "_capture_owned_process", lambda _application: process)
    monkeypatch.setattr(rebuild, "_ensure_owned_process_exited", checked.append)

    with pytest.raises(RuntimeError, match="quit failed"):
        rebuild._close_application(application)

    assert checked == [process]
    assert application.quit_calls == 1
