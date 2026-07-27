from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_live_operation as live_operation  # noqa: E402
from hwp_live_api import LiveHwpApplication  # noqa: E402
from hwp_live_native_action_contract import (  # noqa: E402
    NativeActionFailure,
    NativeActionFailureEvidence,
)
from hwp_live_native_action_commands import (  # noqa: E402
    NativeCharacterFormat,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
)
from hwp_live_native_action_results import (  # noqa: E402
    NativeActionResult,
    NativeSnapshot,
)
from hwp_live_native_action_models import NativeActionRequest  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_native_failure_result import native_action_failure_result  # noqa: E402
from hwp_operation_contract import OperationResult  # noqa: E402


_DOCUMENT_ID = 17
_DOCUMENT_PATH = "C:/documents/sample.hwp"
_NATIVE_SUCCESS = NativeActionResult(
    commands_executed=1,
    actions_executed=1,
    text_insertions=0,
    image_insertions=0,
    elapsed_microseconds=25,
    created_control_ids=(),
)
_CHARACTER_FORMAT = NativeCharacterFormat(
    face_name="함초롬바탕",
    height_hwpunit=1_000,
    bold=False,
    text_color=0,
)
_PARAGRAPH_FORMAT = NativeParagraphFormat(
    alignment=0,
    line_spacing=160,
    left_margin_hwpunit=0,
    right_margin_hwpunit=0,
    indentation_hwpunit=0,
    previous_spacing_hwpunit=0,
    next_spacing_hwpunit=0,
)


def _candidate() -> HwpDocumentCandidate:
    return cast(
        HwpDocumentCandidate,
        cast(
            object,
            SimpleNamespace(
                selector="document-selector",
                document_id=_DOCUMENT_ID,
                full_name=_DOCUMENT_PATH,
                window_handle=100,
                document=SimpleNamespace(
                    DocumentID=_DOCUMENT_ID,
                    FullName=_DOCUMENT_PATH,
                ),
            ),
        ),
    )


def _snapshot(
    cursor: NativePosition,
    *,
    current_page: int,
    page_count: int = 4,
    selection: NativeSelection | None = None,
) -> NativeSnapshot:
    return NativeSnapshot(
        document_id=_DOCUMENT_ID,
        full_name=_DOCUMENT_PATH,
        current_page=current_page,
        page_count=page_count,
        modified=False,
        cursor=cursor,
        selection=(
            NativeSelection(
                selected=False,
                start=cursor,
                end=cursor,
            )
            if selection is None
            else selection
        ),
        selected_text="",
        control_type="",
        control_instance_id="",
        cell_address="",
        style_id=0,
        character_format=_CHARACTER_FORMAT,
        paragraph_format=_PARAGRAPH_FORMAT,
    )


def _operate(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    snapshots: tuple[NativeSnapshot | None, ...],
) -> OperationResult:
    pending_snapshots = iter(snapshots)

    def execute_native_actions(
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        assert window_handle == 100
        assert request is not None
        assert minimum_version == 9
        return _NATIVE_SUCCESS

    def read_native_snapshot(window_handle: int) -> NativeSnapshot | None:
        return next(pending_snapshots) if window_handle == 100 else None

    monkeypatch.setattr(
        live_operation,
        "execute_native_actions",
        execute_native_actions,
    )
    monkeypatch.setattr(
        live_operation,
        "read_native_snapshot",
        read_native_snapshot,
    )
    return live_operation.operate_validated(
        cast(LiveHwpApplication, object()),
        _candidate(),
        f"action:{action}",
        {},
        resolve_only=False,
        allow_document_change=True,
        use_defaults=False,
        expected_cursor=None,
        unsafe_selectors=set(),
        guard=lambda: None,
    )


@pytest.mark.parametrize(
    ("action", "before", "after"),
    [
        (
            "MoveDocBegin",
            _snapshot(NativePosition(0, 4, 8), current_page=3),
            _snapshot(NativePosition(0, 0, 0), current_page=1),
        ),
        (
            "MoveParaBegin",
            _snapshot(NativePosition(2, 4, 8), current_page=2),
            _snapshot(NativePosition(2, 4, 0), current_page=2),
        ),
        (
            "MoveNextParaBegin",
            _snapshot(NativePosition(2, 4, 8), current_page=2),
            _snapshot(NativePosition(2, 5, 0), current_page=2),
        ),
        (
            "MovePageDown",
            _snapshot(NativePosition(0, 4, 8), current_page=2),
            _snapshot(NativePosition(0, 8, 0), current_page=3),
        ),
        (
            "MoveSelParaBegin",
            _snapshot(NativePosition(2, 4, 8), current_page=2),
            _snapshot(
                NativePosition(2, 4, 0),
                current_page=2,
                selection=NativeSelection(
                    selected=True,
                    start=NativePosition(2, 4, 0),
                    end=NativePosition(2, 4, 8),
                ),
            ),
        ),
    ],
)
def test_atomic_movement_and_selection_actions_require_matching_snapshot_readback(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    before: NativeSnapshot,
    after: NativeSnapshot,
) -> None:
    result = _operate(monkeypatch, action, (before, after))

    assert result.status == "executed"
    assert result.verified is True
    assert result.verification == "native_snapshot_before_after"
    assert result.native_protocol == 9
    assert result.current_page == after.current_page
    assert result.page_count == after.page_count


@pytest.mark.parametrize(
    "after",
    [
        _snapshot(NativePosition(0, 1, 0), current_page=1),
        None,
    ],
    ids=("mismatched-postcondition", "missing-readback"),
)
def test_atomic_movement_action_is_unverified_when_readback_does_not_confirm_it(
    monkeypatch: pytest.MonkeyPatch,
    after: NativeSnapshot | None,
) -> None:
    before = _snapshot(NativePosition(0, 4, 8), current_page=3)

    result = _operate(monkeypatch, "MoveDocBegin", (before, after))

    assert result.status == "executed"
    assert result.verified is False
    assert result.verification == "native_snapshot_before_after"


def test_atomic_action_without_certified_postcondition_does_not_become_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def execute_native_actions(
        _window_handle: int,
        _request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        assert minimum_version == 9
        return _NATIVE_SUCCESS

    def unexpected_snapshot(_window_handle: int) -> NativeSnapshot | None:
        pytest.fail(
            "an action without a certified readback must not claim snapshot verification"
        )

    monkeypatch.setattr(
        live_operation,
        "execute_native_actions",
        execute_native_actions,
    )
    monkeypatch.setattr(
        live_operation,
        "read_native_snapshot",
        unexpected_snapshot,
    )

    result = live_operation.operate_validated(
        cast(LiveHwpApplication, object()),
        _candidate(),
        "action:Cancel",
        {},
        resolve_only=False,
        allow_document_change=True,
        use_defaults=False,
        expected_cursor=None,
        unsafe_selectors=set(),
        guard=lambda: None,
    )

    assert result.status == "executed"
    assert result.verified is False
    assert result.verification == "native_action_result"


@pytest.mark.parametrize("minimum_native_protocol", [9, 11])
def test_native_failure_result_reports_the_execution_paths_minimum_protocol(
    minimum_native_protocol: Literal[9, 11],
) -> None:
    failure = NativeActionFailure(
        NativeActionFailureEvidence(
            code="ACTION_FAILED",
            location="MoveDocBegin",
            message="returned false",
            commands_completed=0,
            failed_step="RUN MoveDocBegin",
            partial_mutation=False,
            retry_safe=True,
            structure_digest_before=None,
            structure_digest_after=None,
        )
    )

    result = native_action_failure_result(
        "atomic action",
        failure,
        minimum_native_protocol=minimum_native_protocol,
    )

    assert result.native_protocol == minimum_native_protocol
