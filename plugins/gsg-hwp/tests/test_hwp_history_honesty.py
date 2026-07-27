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
import hwp_live_native_history as native_history  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_api import HwpComApplication, HwpComDocument  # noqa: E402
from hwp_live_document_edit_verification import (  # noqa: E402
    HistoryStructureSnapshot,
)
from hwp_live_edit_history import LiveEditHistoryStore  # noqa: E402
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
from hwp_live_native_history import execute_native_history  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_operation_contract import HwpWorkflowId, WorkflowResolution  # noqa: E402
from hwp_operation_verification import enforce_operation_verification  # noqa: E402


FULL_NAME = "C:/documents/history-honesty.hwp"


def _state(
    *,
    page_count: int = 2,
    modified: int = 1,
    caret: tuple[int, int, int] = (0, 0, 0),
    control_count: int = 3,
    control_hash: int = 987,
) -> str:
    list_id, paragraph, character = caret
    return (
        f"{page_count}\t{modified}\t{list_id}\t{paragraph}\t{character}\t"
        f"{control_count}\t{control_hash}"
    )


def _automation_response(
    *,
    method: str = "Undo",
    value: str = "1",
    before: str | None = None,
    after: str | None = None,
    elapsed: int = 40,
) -> str:
    # HCV1 AUTOMATION envelope: owner, name, member_kind, four HRESULTs,
    # variant type, value, seven before fields, seven after fields, elapsed.
    return (
        "HCV1\tAUTOMATION\tIXHwpDocument\t"
        f"{method}\tmethod\t0\t0\t0\t0\t11\t{value}\t"
        f"{before if before is not None else _state()}\t"
        f"{after if after is not None else _state()}\t"
        f"{elapsed}"
    )


def _candidate() -> HwpDocumentCandidate:
    return HwpDocumentCandidate(
        selector="history-document",
        moniker_name="!HwpObject.7",
        application=cast(HwpComApplication, object()),
        document=cast(HwpComDocument, object()),
        document_id=7,
        full_name=FULL_NAME,
        document_format="HWP",
        edit_mode=1,
        window_handle=41,
        active=True,
    )


def _request(
    workflow: HwpWorkflowId = "document.undo",
    *,
    steps: int = 1,
) -> recipe.NativeDocumentEditRequest:
    return recipe.NativeDocumentEditRequest(
        candidate=_candidate(),
        history=LiveEditHistoryStore(),
        routing_page=NativePageInspection(7, FULL_NAME, 1, 2, "현재 본문", ()),
        resolution=WorkflowResolution(
            query="한컴 실행 이력",
            status="resolved",
            lookup_microseconds=0,
            workflow_id=workflow,
            steps=("ResolveHistory", "VerifySnapshot"),
            match_kind="explicit",
        ),
        target=None,
        parameters={"steps": steps},
        resolve_only=False,
        allow_document_change=True,
    )


def _native_snapshot() -> NativeSnapshot:
    position = NativePosition(0, 0, 0)
    return NativeSnapshot(
        document_id=7,
        full_name=FULL_NAME,
        current_page=1,
        page_count=2,
        modified=True,
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


def _structure(token: str) -> HistoryStructureSnapshot:
    return HistoryStructureSnapshot(
        document_id=7,
        full_name=FULL_NAME,
        page=1,
        page_count=2,
        state_token=token,
    )


def _wire_recipe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    history_result: object,
    tokens: tuple[str, str],
) -> None:
    snapshots = iter((_native_snapshot(), _native_snapshot()))
    structures = iter((_structure(tokens[0]), _structure(tokens[1])))
    monkeypatch.setattr(recipe, "read_native_snapshot", lambda _h: next(snapshots))
    monkeypatch.setattr(recipe, "execute_document_edit_history", lambda *_a: None)
    monkeypatch.setattr(recipe, "execute_native_history", lambda *_a: history_result)
    monkeypatch.setattr(
        recipe,
        "capture_history_structure_snapshot",
        lambda *_a: next(structures),
    )


# --- native step evidence -------------------------------------------------


def test_undo_returning_false_is_reported_as_empty_history_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_history,
        "probe_official_api",
        lambda *_a: _automation_response(value="0"),
    )

    result = execute_native_history(41, "undo", 3)

    assert result.applied == 0
    assert result.exhausted is True


def test_selection_only_undo_is_not_counted_as_a_content_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # HWP undid something and moved the caret, but page count, control count
    # and control hash are identical. This is the "선택 영역만 해제" case.
    monkeypatch.setattr(
        native_history,
        "probe_official_api",
        lambda *_a: _automation_response(
            before=_state(caret=(0, 12, 40)),
            after=_state(caret=(0, 3, 0)),
        ),
    )

    result = execute_native_history(41, "undo", 1)

    assert result.applied == 1
    assert result.content_changed is False


def test_structural_undo_is_counted_as_a_content_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_history,
        "probe_official_api",
        lambda *_a: _automation_response(
            before=_state(control_count=3),
            after=_state(control_count=4),
        ),
    )

    result = execute_native_history(41, "undo", 1)

    assert result.applied == 1
    assert result.content_changed is True


def test_exhausted_history_stops_early_and_reports_applied_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        (
            _automation_response(value="1"),
            _automation_response(value="1"),
            _automation_response(value="0"),
        )
    )
    calls = 0

    def probe(*_args: object) -> str:
        nonlocal calls
        calls += 1
        return next(responses)

    monkeypatch.setattr(native_history, "probe_official_api", probe)

    result = execute_native_history(41, "undo", 5)

    assert result.applied == 2
    assert result.steps == 5
    assert result.exhausted is True
    # Stopped at the empty stack instead of spending the remaining round trips.
    assert calls == 3


# --- recipe reporting -----------------------------------------------------


def test_recipe_reports_no_history_instead_of_success_or_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exhausted = native_history.NativeHistoryResult("undo", 1, 12, 0, False)
    _wire_recipe(monkeypatch, history_result=exhausted, tokens=("a" * 64, "a" * 64))

    result = recipe.operate_native_document_edit(_request())

    assert result is not None
    assert result.status == "unsupported"
    assert result.changed is False
    assert "되돌릴 한컴 실행 이력이 없습니다" in result.message


def test_recipe_reports_the_steps_that_actually_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partial = native_history.NativeHistoryResult("undo", 5, 30, 2, False)
    _wire_recipe(monkeypatch, history_result=partial, tokens=("a" * 64, "b" * 64))

    result = recipe.operate_native_document_edit(_request(steps=5))

    assert result is not None
    assert result.status == "executed"
    assert result.commands_executed == 2
    assert "2단계" in result.message
    assert "요청한 5단계" in result.message


def test_recipe_refuses_success_when_document_did_not_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The engine said it undid a step, but neither the native content state nor
    # the routing page token moved. This is the reported defect: it must not be
    # reported as a restored edit.
    selection_only = native_history.NativeHistoryResult("undo", 1, 12, 1, False)
    _wire_recipe(
        monkeypatch,
        history_result=selection_only,
        tokens=("c" * 64, "c" * 64),
    )

    with pytest.raises(HwpLiveError):
        _ = recipe.operate_native_document_edit(_request())


def test_recipe_accepts_offpage_change_proved_by_native_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Safety direction: the undo really happened, but on a page the routing
    # inspection does not cover, so the token is unchanged. The native content
    # state proves the change, so this must not be reported as a failure.
    offpage = native_history.NativeHistoryResult("undo", 1, 12, 1, True)
    _wire_recipe(monkeypatch, history_result=offpage, tokens=("d" * 64, "d" * 64))

    result = recipe.operate_native_document_edit(_request())

    assert result is not None
    assert result.status == "executed"
    assert result.verified is True
    assert enforce_operation_verification("document.undo", result).verified is True


def test_recipe_reports_success_when_the_page_token_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied = native_history.NativeHistoryResult("undo", 1, 12, 1, False)
    _wire_recipe(monkeypatch, history_result=applied, tokens=("e" * 64, "f" * 64))

    result = recipe.operate_native_document_edit(_request())

    assert result is not None
    assert result.status == "executed"
    assert result.verified is True
    assert result.commands_executed == 1
    assert "1단계" in result.message
