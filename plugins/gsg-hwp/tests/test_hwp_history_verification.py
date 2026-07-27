from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_live_document_edit_recipe as recipe  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_api import HwpComApplication, HwpComDocument  # noqa: E402
from hwp_live_edit_history import LiveEditHistoryStore  # noqa: E402
from hwp_live_document_edit_verification import (  # noqa: E402
    HistoryStructureSnapshot,
)
from hwp_live_native_action_commands import (  # noqa: E402
    NativeCharacterFormat,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
)
from hwp_live_native_action_models import (  # noqa: E402
    NativePageInspection,
    NativeSnapshot,
)
from hwp_live_native_history import NativeHistoryResult  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_operation_contract import HwpWorkflowId, WorkflowResolution  # noqa: E402
from hwp_operation_verification import enforce_operation_verification  # noqa: E402


def _history_request(workflow: HwpWorkflowId) -> recipe.NativeDocumentEditRequest:
    full_name = "C:/documents/history-verification.hwp"
    return recipe.NativeDocumentEditRequest(
        candidate=HwpDocumentCandidate(
            selector="history-document",
            moniker_name="!HwpObject.7",
            application=cast(HwpComApplication, object()),
            document=cast(HwpComDocument, object()),
            document_id=7,
            full_name=full_name,
            document_format="HWP",
            edit_mode=1,
            window_handle=41,
            active=True,
        ),
        history=LiveEditHistoryStore(),
        routing_page=NativePageInspection(7, full_name, 1, 2, "현재 본문", ()),
        resolution=WorkflowResolution(
            query="한컴 실행 이력",
            status="resolved",
            lookup_microseconds=0,
            workflow_id=workflow,
            steps=("ResolveHistory", "VerifySnapshot"),
            match_kind="explicit",
        ),
        target=None,
        parameters={"steps": 1},
        resolve_only=False,
        allow_document_change=True,
    )


def _native_snapshot(*, modified: bool) -> NativeSnapshot:
    position = NativePosition(0, 0, 0)
    return NativeSnapshot(
        document_id=7,
        full_name="C:/documents/history-verification.hwp",
        current_page=1,
        page_count=2,
        modified=modified,
        cursor=position,
        selection=NativeSelection(False, position, position),
        selected_text="",
        control_type="",
        control_instance_id="",
        cell_address="",
        style_id=0,
        character_format=NativeCharacterFormat("", 1000, False, 0),
        paragraph_format=NativeParagraphFormat(0, 100, 0, 0, 0, 0, 0),
    )


def _structure_snapshot(token: str) -> HistoryStructureSnapshot:
    return HistoryStructureSnapshot(
        document_id=7,
        full_name="C:/documents/history-verification.hwp",
        page=1,
        page_count=2,
        state_token=token,
    )


@pytest.mark.parametrize("workflow", ("document.undo", "document.redo"))
def test_native_history_supplies_structure_readback_to_verification_gate(
    monkeypatch: pytest.MonkeyPatch,
    workflow: HwpWorkflowId,
) -> None:
    native_snapshots = iter(
        (_native_snapshot(modified=True), _native_snapshot(modified=True))
    )
    structure_snapshots = iter(
        (_structure_snapshot("a" * 64), _structure_snapshot("b" * 64))
    )

    def read_snapshot(_handle: int) -> NativeSnapshot:
        return next(native_snapshots)

    def no_managed_history(*_args: object) -> None:
        return None

    def execute_history(*_args: object) -> NativeHistoryResult:
        return NativeHistoryResult("undo", 1, 17)

    def capture_structure(*_args: object) -> HistoryStructureSnapshot:
        return next(structure_snapshots)

    monkeypatch.setattr(recipe, "read_native_snapshot", read_snapshot)
    monkeypatch.setattr(recipe, "execute_document_edit_history", no_managed_history)
    monkeypatch.setattr(recipe, "execute_native_history", execute_history)
    monkeypatch.setattr(
        recipe,
        "capture_history_structure_snapshot",
        capture_structure,
    )

    result = recipe.operate_native_document_edit(_history_request(workflow))

    assert result is not None
    assert result.status == "executed"
    assert result.verification == "native_snapshot_before_after"
    assert result.verified is True
    assert enforce_operation_verification(workflow, result).verified is True


def test_native_history_rejects_success_when_structure_did_not_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_snapshots = iter(
        (_native_snapshot(modified=False), _native_snapshot(modified=False))
    )
    unchanged = _structure_snapshot("c" * 64)

    def read_snapshot(_handle: int) -> NativeSnapshot:
        return next(native_snapshots)

    def no_managed_history(*_args: object) -> None:
        return None

    def execute_history(*_args: object) -> NativeHistoryResult:
        return NativeHistoryResult("undo", 1, 11)

    def capture_structure(*_args: object) -> HistoryStructureSnapshot:
        return unchanged

    monkeypatch.setattr(recipe, "read_native_snapshot", read_snapshot)
    monkeypatch.setattr(recipe, "execute_document_edit_history", no_managed_history)
    monkeypatch.setattr(recipe, "execute_native_history", execute_history)
    monkeypatch.setattr(
        recipe,
        "capture_history_structure_snapshot",
        capture_structure,
    )

    with pytest.raises(HwpLiveError, match="이력이 없거나"):
        _ = recipe.operate_native_document_edit(_history_request("document.undo"))
