from __future__ import annotations

import asyncio
import base64
import json
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Final, cast


SCRIPTS: Final = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_official_api_catalog import load_official_api_catalog  # noqa: E402
from hwp_official_api_contract import (  # noqa: E402
    OfficialAction,
    OfficialApiCatalog,
    OfficialAutomationMember,
)
from hwp_official_api_evidence import (  # noqa: E402
    classify_native_evidence,
    parse_native_response,
)
from hwp_official_api_policy import (  # noqa: E402
    enabled_official_api_requests,
    official_api_policy_decision,
    official_api_policy_error_response,
)
from hwp_official_api_requests import (  # noqa: E402
    OfficialApiNativeRequest,
    OfficialApiSafety,
    build_official_api_requests,
    classify_official_action_safety,
    classify_official_automation_safety,
)
from hwp_official_api_live import (  # noqa: E402
    OfficialApiLiveBatchResult,
    OfficialApiLiveCategory,
    OfficialApiLiveItem,
)
from hwp_mcp_qa_handlers import McpQaHandlers  # noqa: E402
from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_mcp_dispatch import McpThreadDispatcher  # noqa: E402


_ACTIVE_DESTRUCTIVE_ACTIONS: Final = frozenset(
    {
        "FileClose",
        "FileQuit",
        "FileSave",
        "FileSaveAs",
        "HwpCtrlFileSave",
        "HwpCtrlFileSaveAs",
        "HwpCtrlFileSaveAsAutoBlock",
        "HwpCtrlFileSaveAutoBlock",
        "VersionSave",
    }
)
_ACTIVE_DESTRUCTIVE_AUTOMATION: Final = frozenset(
    {
        ("IHwpObject", "Save"),
        ("IHwpObject", "SaveAs"),
        ("IHwpObject", "Clear"),
        ("IXHwpDocuments", "Close"),
        ("IXHwpDocument", "Close"),
        ("IXHwpDocument", "Save"),
        ("IXHwpDocument", "SaveAs"),
        ("IXHwpDocument", "Clear"),
        ("IXHwpWindows", "Close"),
        ("IXHwpWindow", "Close"),
        ("IXHwpTabs", "Close"),
        ("IXHwpTab", "Close"),
    }
)
_ACTIVE_INDIRECT_ACTIONS: Final = frozenset(
    {
        *(f"MacroPlay{index}" for index in range(1, 12)),
        "MacroRepeat",
        "MacroRepeatDlg",
        "ScrMacroRepeatDlg",
    }
)


def _requests():
    return enabled_official_api_requests(
        build_official_api_requests(load_official_api_catalog())
    )


def test_catalog_metadata_marks_the_exact_active_data_loss_boundary() -> None:
    requests = _requests()
    destructive_actions = frozenset(
        request.name
        for request in requests
        if request.category == "action" and request.safety.destructive
    )
    destructive_automation = frozenset(
        (request.owner, request.name)
        for request in requests
        if request.category == "automation" and request.safety.destructive
    )

    assert destructive_actions == _ACTIVE_DESTRUCTIVE_ACTIONS
    assert destructive_automation == _ACTIVE_DESTRUCTIVE_AUTOMATION
    assert (
        frozenset(
            request.name
            for request in requests
            if request.category == "action" and request.safety.indirect_execution
        )
        == _ACTIVE_INDIRECT_ACTIONS
    )

    safe_action_names = {
        "Close",
        "CloseEx",
        "DrawObjOpenClosePolygon",
        "FileSaveAsImage",
        "PictureSave",
        "StyleClearCharStyle",
    }
    assert all(
        not request.safety.destructive
        for request in requests
        if request.category == "action" and request.name in safe_action_names
    )
    quit_event = next(
        request
        for request in requests
        if request.category == "automation"
        and request.owner == "IHwpObjectEvents"
        and request.name == "Quit"
    )
    assert quit_event.member_kind == "event"
    assert not quit_event.safety.destructive


def test_policy_uses_effect_metadata_instead_of_known_api_names() -> None:
    template = next(
        request for request in _requests() if not request.safety.destructive
    )
    future_destructive = replace(
        template,
        case_id="automation:9999:IFutureWorkspace.ArchiveCurrentDocument",
        name="ArchiveCurrentDocument",
        owner="IFutureWorkspace",
        safety=OfficialApiSafety(
            effect="document_discard",
            target_scope="document",
            classification="catalog_metadata",
            rationale="synthetic future catalog classification",
        ),
    )
    familiar_but_safe = replace(
        template,
        case_id="action:9998:FileQuit",
        name="FileQuit",
        safety=OfficialApiSafety(
            effect="none",
            target_scope="artifact",
            classification="catalog_metadata",
            rationale="synthetic non-destructive catalog classification",
        ),
    )

    blocked = official_api_policy_decision(future_destructive)
    missing_isolation = official_api_policy_decision(
        future_destructive,
        destructive_opt_in=True,
    )
    caller_asserted_isolation = official_api_policy_decision(
        future_destructive,
        destructive_opt_in=True,
        owned_isolation_verified=True,
    )
    server_owned_destructive = replace(
        future_destructive,
        safety=replace(
            future_destructive.safety,
            fixture_scope="server_owned_process_document",
            fixture_isolation_guaranteed=True,
        ),
    )
    verified_isolation = official_api_policy_decision(
        server_owned_destructive,
        destructive_opt_in=True,
        owned_isolation_verified=True,
    )
    unverified_arguments = official_api_policy_decision(
        replace(
            server_owned_destructive,
            safety=replace(
                server_owned_destructive.safety,
                arguments_fail_safe=False,
            ),
        ),
        destructive_opt_in=True,
        owned_isolation_verified=True,
    )

    assert not blocked.allowed
    assert blocked.code == "destructive_api_blocked"
    assert not missing_isolation.allowed
    assert missing_isolation.code == "owned_isolation_required"
    assert not caller_asserted_isolation.allowed
    assert caller_asserted_isolation.code == "owned_isolation_required"
    assert verified_isolation.allowed
    assert verified_isolation.code == "allowed_owned_isolation"
    assert not unverified_arguments.allowed
    assert unverified_arguments.code == "destructive_argument_unverified"
    assert official_api_policy_decision(familiar_but_safe).allowed


def test_request_without_catalog_safety_metadata_is_fail_closed() -> None:
    request = OfficialApiNativeRequest(
        case_id="automation:9999:IFutureWorkspace.Unknown",
        category="automation",
        name="Unknown",
        owner="IFutureWorkspace",
        member_kind="method",
        source_page=1,
        payload="HCV1\nAUTOMATION\tIFutureWorkspace\tUnknown\tmethod\nEND",
        input_lines=(),
    )

    decision = official_api_policy_decision(request)

    assert not decision.allowed
    assert decision.code == "official_api_unclassified"


def test_new_catalog_names_inherit_destructive_semantics_from_metadata() -> None:
    future_action = OfficialAction(
        name="ArchiveCurrentWorkspace",
        parameter_set=None,
        description="현재 문서를 저장한다.",
        remarks="",
        source_page=1,
    )
    future_automation = OfficialAutomationMember(
        owner="IFutureWorkspace",
        name="RetireCurrentWorkspace",
        member_kind="method",
        description="현재 문서를 닫는다.",
        declaration="BOOL RetireCurrentWorkspace(BOOL isDirty)",
        details="현재 문서를 닫는다.",
        source_page_start=1,
        source_page_end=1,
    )
    future_termination = OfficialAutomationMember(
        owner="IFutureWorkspace",
        name="TerminateCurrentDocument",
        member_kind="method",
        description="현재 문서를 종료한다.",
        declaration="void TerminateCurrentDocument(void)",
        details="현재 문서를 종료한다.",
        source_page_start=1,
        source_page_end=1,
    )
    save_path_query = OfficialAutomationMember(
        owner="IFutureWorkspace",
        name="GetDocumentSavePath",
        member_kind="method",
        description="현재 문서 저장 경로를 얻는다.",
        declaration="BSTR GetDocumentSavePath(void)",
        details="현재 문서 저장 경로를 얻는다.",
        source_page_start=1,
        source_page_end=1,
    )
    close_query = OfficialAutomationMember(
        owner="IFutureWorkspace",
        name="CanCloseDocument",
        member_kind="method",
        description="현재 문서를 닫을 수 있는지 확인한다.",
        declaration="BOOL CanCloseDocument(void)",
        details="현재 문서를 닫을 수 있는지 확인한다.",
        source_page_start=1,
        source_page_end=1,
    )
    getter_with_destructive_effect = OfficialAutomationMember(
        owner="IFutureWorkspace",
        name="GetAndCloseDocument",
        member_kind="method",
        description="현재 문서를 닫고 닫은 문서 정보를 반환한다.",
        declaration="BOOL GetAndCloseDocument(void)",
        details="현재 문서를 닫고 닫은 문서 정보를 반환한다.",
        source_page_start=1,
        source_page_end=1,
    )
    details_only_destructive = OfficialAutomationMember(
        owner="IFutureWorkspace",
        name="RetireWorkspaceItem",
        member_kind="method",
        description="현재 워크스페이스 항목을 처리한다.",
        declaration="void RetireWorkspaceItem(void)",
        details="이 메서드는 현재 문서를 닫는다.",
        source_page_start=1,
        source_page_end=1,
    )

    action_safety = classify_official_action_safety(future_action)
    automation_safety = classify_official_automation_safety(future_automation)
    termination_safety = classify_official_automation_safety(future_termination)

    assert action_safety.effect == "document_save"
    assert action_safety.destructive
    assert automation_safety.effect == "document_close"
    assert automation_safety.destructive
    assert termination_safety.effect == "document_close"
    assert not classify_official_automation_safety(save_path_query).destructive
    assert not classify_official_automation_safety(close_query).destructive
    assert (
        classify_official_automation_safety(getter_with_destructive_effect).effect
        == "document_close"
    )
    assert (
        classify_official_automation_safety(details_only_destructive).effect
        == "document_close"
    )


def test_destructive_boolean_inputs_are_fail_safe_without_global_rewrite() -> None:
    requests = _requests()
    close_requests = tuple(
        request
        for request in requests
        if request.category == "automation"
        and request.name == "Close"
        and request.member_kind == "method"
    )
    save_requests = tuple(
        request
        for request in requests
        if request.category == "automation"
        and request.name == "Save"
        and request.member_kind == "method"
    )
    add_document = next(
        request
        for request in requests
        if request.category == "automation"
        and request.owner == "IXHwpDocuments"
        and request.name == "Add"
    )

    assert len(close_requests) == 6
    assert all("ARG\tBOOL\t1" in request.input_lines for request in close_requests)
    assert all("ARG\tBOOL\t1" in request.input_lines for request in save_requests)
    assert "ARG\tBOOL\t0" in add_document.input_lines


def test_unverified_destructive_bool_marker_and_policy_error_have_distinct_roles() -> None:
    member = OfficialAutomationMember(
        owner="IFutureDocument",
        name="Close",
        member_kind="method",
        description="현재 문서를 닫는다.",
        declaration="BOOL Close(BOOL saveChanges)",
        details="saveChanges 인자의 안전한 값은 설명하지 않는다.",
        source_page_start=1,
        source_page_end=1,
    )
    catalog = OfficialApiCatalog(
        schema_version=1,
        sources=(),
        actions=(),
        parameter_sets=(),
        automation_members=(member,),
    )

    request = build_official_api_requests(catalog)[0]
    decision = official_api_policy_decision(
        request,
        destructive_opt_in=True,
        owned_isolation_verified=True,
    )
    response = official_api_policy_error_response(decision)

    assert request.input_lines == (
        "ARG\tUNSUPPORTED\tDESTRUCTIVE_BOOL_FAIL_SAFE_UNVERIFIED",
    )
    assert not request.safety.arguments_fail_safe
    assert (
        request.safety.argument_safety_issue
        == "destructive_bool_fail_safe_unverified"
    )
    assert not decision.allowed
    assert decision.code == "destructive_argument_unverified"
    assert "파괴적 BOOL 인자의 fail-safe 값을 증명하지 못해" in decision.reason
    assert response.startswith("HCV1\tERROR\tDESTRUCTIVE_ARGUMENT_UNVERIFIED\t")
    assert "DESTRUCTIVE_BOOL_FAIL_SAFE_UNVERIFIED" not in response


def test_evidence_states_that_owner_fixture_does_not_guarantee_isolation() -> None:
    response_fields = [
        "HCV1",
        "ACTION",
        "CharShapeBold",
        "0",
        "0",
        "0",
        "0",
        "0",
        "1",
        "0",
        "0",
        "1",
        "-1",
        "-2",
        *("1", "0", "0", "0", "0", "0", "0"),
        *("1", "0", "0", "0", "0", "0", "0"),
        "10",
    ]

    evidence = parse_native_response("\t".join(response_fields))

    assert evidence["fixture_isolation_guaranteed"] is False
    assert evidence["fixture_scope"] == "active_document_owner_context"
    assert evidence["operation_effect_verified"] is False
    assert evidence["evidence_scope"] == "invoke_and_state_observation_only"


def test_evidence_rejects_false_returns_and_lost_document_state() -> None:
    valid_state = {
        "page_count": 1,
        "modified": 1,
        "list": 0,
        "paragraph": 0,
        "character": 0,
        "control_count": 0,
        "control_hash": 0,
    }
    invalid_state = {**valid_state, "page_count": -1}
    false_action = {
        "kind": "action",
        "create_action_hresult": 0,
        "execute_hresult": 0,
        "execute_return": 0,
        "run_hresult": -1,
        "run_return": -2,
        "before": valid_state,
        "after": valid_state,
    }
    failed_parameter_execute = {
        "kind": "parameter_set",
        "create_set_hresult": 0,
        "execute_hresult": -1,
        "execute_return": -2,
        "item_count": 1,
        "passed_items": 1,
        "failed_items": 0,
        "before": valid_state,
        "after": valid_state,
    }
    lost_automation_document = {
        "kind": "automation",
        "name": "ReplaceFont",
        "member_kind": "method",
        "owner_hresult": 0,
        "argument_hresult": 0,
        "invoke_hresult": 0,
        "variant_type": 11,
        "value": "True",
        "before": valid_state,
        "after": invalid_state,
    }
    false_mutating_automation = {
        **lost_automation_document,
        "after": valid_state,
        "value": "False",
    }
    false_query_automation = {
        **false_mutating_automation,
        "name": "FieldExist",
    }

    assert classify_native_evidence(false_action) == "failed"
    assert classify_native_evidence(failed_parameter_execute) == "failed"
    assert classify_native_evidence(lost_automation_document) == "failed"
    assert classify_native_evidence(false_mutating_automation) == "failed"
    assert classify_native_evidence(false_query_automation) == "passed"


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[object, tuple[object, ...]]] = []

    async def run(
        self,
        function: Callable[..., object],
        *args: object,
    ) -> object:
        self.calls.append((function, args))
        return function(*args)


class _OfficialApiBridge:
    def __init__(self) -> None:
        self.raw_payloads: list[tuple[int, str]] = []
        self.batch_calls: list[tuple[object, ...]] = []

    @staticmethod
    def _batch(
        category: OfficialApiLiveCategory,
        start: int,
        limit: int,
    ) -> OfficialApiLiveBatchResult:
        requests = tuple(
            request for request in _requests() if request.category == category
        )
        selected = requests[start - 1 : start - 1 + limit]
        items = tuple(
            OfficialApiLiveItem(
                ordinal=start + offset,
                case_id=request.case_id,
                category=category,
                name=request.name,
                owner=request.owner,
                member_kind=request.member_kind,
                source_page=request.source_page,
                input_lines=request.input_lines,
                status="passed",
                wall_ms=0,
                native_elapsed_us=0,
                native_response="safe",
                evidence_json="{}",
                error=None,
            )
            for offset, request in enumerate(selected)
        )
        return OfficialApiLiveBatchResult(
            category=category,
            start=start,
            requested=limit,
            executed=len(items),
            total_in_category=len(requests),
            passed=len(items),
            failed=0,
            unavailable=0,
            transport_errors=0,
            wall_ms=0,
            items=items,
        )

    def run_official_api_batch(
        self,
        session_id: str,
        category: OfficialApiLiveCategory,
        start: int,
        limit: int,
    ) -> OfficialApiLiveBatchResult:
        self.batch_calls.append((session_id, category, start, limit))
        return self._batch(category, start, limit)

    def probe_official_api_batch(
        self,
        window_handle: int,
        category: OfficialApiLiveCategory,
        start: int,
        limit: int,
    ) -> OfficialApiLiveBatchResult:
        self.batch_calls.append((window_handle, category, start, limit))
        return self._batch(category, start, limit)

    def probe_official_api_payload(self, window_handle: int, payload: str) -> str:
        self.raw_payloads.append((window_handle, payload))
        return "safe"


def _category_position(category: str, name: str, owner: str | None = None) -> int:
    requests = tuple(request for request in _requests() if request.category == category)
    return next(
        index
        for index, request in enumerate(requests, 1)
        if request.name == name and (owner is None or request.owner == owner)
    )


def _handlers(
    bridge: _OfficialApiBridge,
    dispatcher: _RecordingDispatcher,
) -> McpQaHandlers:
    return McpQaHandlers(
        cast(HancomBridge, cast(object, bridge)),
        cast(McpThreadDispatcher, cast(object, dispatcher)),
    )


def test_batch_handlers_block_only_destructive_items_before_dispatch() -> None:
    bridge = _OfficialApiBridge()
    dispatcher = _RecordingDispatcher()
    handlers = _handlers(bridge, dispatcher)
    destructive_start = _category_position("action", "FileClose")
    safe_start = _category_position("action", "CharShapeBold")

    run_blocked = asyncio.run(
        handlers.hwp_run_official_api_batch(
            "session",
            "action",
            destructive_start,
            1,
        )
    )
    probe_blocked = asyncio.run(
        handlers.hwp_probe_official_api_batch(
            1234,
            "action",
            destructive_start,
            1,
        )
    )
    safe = asyncio.run(
        handlers.hwp_probe_official_api_batch(1234, "action", safe_start, 1)
    )

    assert run_blocked.executed == len(run_blocked.items) == 1
    assert probe_blocked.executed == len(probe_blocked.items) == 1
    assert run_blocked.failed == probe_blocked.failed == 1
    assert run_blocked.items[0].native_response is None
    assert run_blocked.items[0].error is not None
    assert "기본 차단" in run_blocked.items[0].error
    blocked_evidence = cast(
        dict[str, object],
        json.loads(cast(str, run_blocked.items[0].evidence_json)),
    )
    assert blocked_evidence["native_invoked"] is False
    assert blocked_evidence["operation_effect_verified"] is False
    assert blocked_evidence["fixture_isolation_guaranteed"] is False
    assert safe.passed == safe.executed == 1
    assert bridge.batch_calls == [(1234, "action", safe_start, 1)]
    assert len(dispatcher.calls) == 1


def test_mixed_batch_executes_safe_slice_and_synthesizes_only_blocked_item() -> None:
    bridge = _OfficialApiBridge()
    dispatcher = _RecordingDispatcher()
    handlers = _handlers(bridge, dispatcher)
    destructive_start = _category_position("action", "FileClose")

    result = asyncio.run(
        handlers.hwp_probe_official_api_batch(
            1234,
            "action",
            destructive_start - 1,
            2,
        )
    )

    assert result.executed == len(result.items) == 2
    assert result.passed == 1
    assert result.failed == 1
    assert (
        result.passed + result.failed + result.unavailable + result.transport_errors
        == result.executed
    )
    assert len(result.items) == 2
    assert result.items[0].status == "passed"
    assert result.items[1].name == "FileClose"
    assert result.items[1].native_response is None
    assert bridge.batch_calls == [(1234, "action", destructive_start - 1, 1)]


def test_raw_handler_blocks_direct_and_delegated_destructive_payloads() -> None:
    bridge = _OfficialApiBridge()
    dispatcher = _RecordingDispatcher()
    handlers = _handlers(bridge, dispatcher)
    encoded_file_quit = base64.b64encode(b"FileQuit").decode("ascii")
    payloads = (
        "HCV1\nACTION\tFileQuit\nEND",
        (
            "HCV1\nAUTOMATION\tIHwpObject\tRun\tmethod\n"
            f"ARG\tBSTR64\t{encoded_file_quit}\nEND"
        ),
        "HCV1\nPARAMETER_SET\tVersionInfo\tVersionSave\nEND",
    )

    responses = tuple(
        asyncio.run(handlers.hwp_probe_official_api_payload(1234, payload))
        for payload in payloads
    )

    assert all(response.startswith("HCV1\tERROR\t") for response in responses)
    assert all("DESTRUCTIVE_API_BLOCKED" in response for response in responses)
    assert not dispatcher.calls
    assert not bridge.raw_payloads


def test_raw_handler_blocks_destructive_action_remapping_before_dispatch() -> None:
    bridge = _OfficialApiBridge()
    dispatcher = _RecordingDispatcher()
    handlers = _handlers(bridge, dispatcher)
    encoded_safe_action = base64.b64encode(b"CharShapeBold").decode("ascii")
    encoded_destructive_action = base64.b64encode(b"FileQuit").decode("ascii")
    payload = (
        "HCV1\nAUTOMATION\tIHwpObject\tReplaceAction\tmethod\n"
        f"ARG\tBSTR64\t{encoded_safe_action}\n"
        f"ARG\tBSTR64\t{encoded_destructive_action}\nEND"
    )
    safe_mapping_payload = (
        "HCV1\nAUTOMATION\tIHwpObject\tReplaceAction\tmethod\n"
        f"ARG\tBSTR64\t{encoded_safe_action}\n"
        f"ARG\tBSTR64\t{encoded_safe_action}\nEND"
    )

    response = asyncio.run(handlers.hwp_probe_official_api_payload(1234, payload))
    safe_mapping_response = asyncio.run(
        handlers.hwp_probe_official_api_payload(1234, safe_mapping_payload)
    )

    assert response.startswith("HCV1\tERROR\tACTION_ROUTING_BLOCKED\t")
    assert safe_mapping_response == "safe"
    assert bridge.raw_payloads == [(1234, safe_mapping_payload)]
    assert len(dispatcher.calls) == 1


def test_raw_handler_blocks_persistent_auto_confirmation_mode() -> None:
    bridge = _OfficialApiBridge()
    dispatcher = _RecordingDispatcher()
    handlers = _handlers(bridge, dispatcher)
    auto_yes_payload = (
        "HCV1\nAUTOMATION\tIHwpObject\tSetMessageBoxMode\tmethod\nARG\tI4\t69648\nEND"
    )
    reset_payload = (
        "HCV1\nAUTOMATION\tIHwpObject\tSetMessageBoxMode\tmethod\nARG\tI4\t0\nEND"
    )

    blocked = asyncio.run(
        handlers.hwp_probe_official_api_payload(1234, auto_yes_payload)
    )
    reset = asyncio.run(handlers.hwp_probe_official_api_payload(1234, reset_payload))

    assert blocked.startswith("HCV1\tERROR\tAUTO_CONFIRMATION_BLOCKED\t")
    assert reset == "safe"
    assert bridge.raw_payloads == [(1234, reset_payload)]
    assert len(dispatcher.calls) == 1


def test_raw_handler_blocks_unbounded_macro_execution() -> None:
    bridge = _OfficialApiBridge()
    dispatcher = _RecordingDispatcher()
    handlers = _handlers(bridge, dispatcher)
    encoded_function = base64.b64encode(b"QASafeMacro").decode("ascii")
    payload = (
        "HCV1\nAUTOMATION\tIHwpObject\tRunScriptMacro\tmethod\n"
        f"ARG\tBSTR64\t{encoded_function}\n"
        "ARG\tI4\t0\nARG\tI4\t0\nEND"
    )
    action_payload = "HCV1\nACTION\tMacroPlay1\nEND"
    encoded_macro_action = base64.b64encode(b"MacroPlay1").decode("ascii")
    delegated_action_payload = (
        "HCV1\nAUTOMATION\tIHwpObject\tRun\tmethod\n"
        f"ARG\tBSTR64\t{encoded_macro_action}\nEND"
    )
    parameter_macro_payloads = (
        "HCV1\nPARAMETER_SET\tKeyMacro\tMacroRepeatDlg\nEND",
        "HCV1\nPARAMETER_SET\tScriptMacro\tScrMacroRepeatDlg\nEND",
    )

    response = asyncio.run(handlers.hwp_probe_official_api_payload(1234, payload))
    action_response = asyncio.run(
        handlers.hwp_probe_official_api_payload(1234, action_payload)
    )
    delegated_action_response = asyncio.run(
        handlers.hwp_probe_official_api_payload(
            1234,
            delegated_action_payload,
        )
    )
    parameter_macro_responses = tuple(
        asyncio.run(handlers.hwp_probe_official_api_payload(1234, macro_payload))
        for macro_payload in parameter_macro_payloads
    )

    assert response.startswith("HCV1\tERROR\tINDIRECT_EXECUTION_BLOCKED\t")
    assert action_response.startswith("HCV1\tERROR\tINDIRECT_EXECUTION_BLOCKED\t")
    assert delegated_action_response.startswith(
        "HCV1\tERROR\tINDIRECT_EXECUTION_BLOCKED\t"
    )
    assert all(
        response.startswith("HCV1\tERROR\tINDIRECT_EXECUTION_BLOCKED\t")
        for response in parameter_macro_responses
    )
    assert not dispatcher.calls
    assert not bridge.raw_payloads


def test_raw_handler_preserves_safe_payload_and_rejects_unverified_opt_in() -> None:
    bridge = _OfficialApiBridge()
    dispatcher = _RecordingDispatcher()
    handlers = _handlers(bridge, dispatcher)
    safe_payload = "HCV1\nACTION\tCharShapeBold\nEND"
    safe_parameter_only_payload = "HCV1\nPARAMETER_SET\tVersionInfo\t-\nEND"
    encoded_file_quit = base64.b64encode(b"FileQuit").decode("ascii")
    safe_lock_payload = (
        "HCV1\nAUTOMATION\tIHwpObject\tLockCommand\tmethod\n"
        f"ARG\tBSTR64\t{encoded_file_quit}\nARG\tBOOL\t0\nEND"
    )
    opt_in_payload = "HCV1\nACTION\tFileClose\nPOLICY\tDESTRUCTIVE_OPT_IN\nEND"
    unsafe_bool_opt_in = (
        "HCV1\nAUTOMATION\tIXHwpDocument\tClose\tmethod\n"
        "ARG\tBOOL\t0\nPOLICY\tDESTRUCTIVE_OPT_IN\nEND"
    )
    fail_safe_bool_opt_in = (
        "HCV1\nAUTOMATION\tIXHwpDocument\tClose\tmethod\n"
        "ARG\tBOOL\t1\nPOLICY\tDESTRUCTIVE_OPT_IN\nEND"
    )

    safe_response = asyncio.run(
        handlers.hwp_probe_official_api_payload(1234, safe_payload)
    )
    safe_parameter_response = asyncio.run(
        handlers.hwp_probe_official_api_payload(
            1234,
            safe_parameter_only_payload,
        )
    )
    safe_lock_response = asyncio.run(
        handlers.hwp_probe_official_api_payload(1234, safe_lock_payload)
    )
    blocked_response = asyncio.run(
        handlers.hwp_probe_official_api_payload(1234, opt_in_payload)
    )
    unsafe_bool_response = asyncio.run(
        handlers.hwp_probe_official_api_payload(1234, unsafe_bool_opt_in)
    )
    fail_safe_bool_response = asyncio.run(
        handlers.hwp_probe_official_api_payload(1234, fail_safe_bool_opt_in)
    )

    assert safe_response == "safe"
    assert safe_parameter_response == "safe"
    assert safe_lock_response == "safe"
    assert bridge.raw_payloads == [
        (1234, safe_payload),
        (1234, safe_parameter_only_payload),
        (1234, safe_lock_payload),
    ]
    assert "OWNED_ISOLATION_REQUIRED" in blocked_response
    assert "DESTRUCTIVE_ARGUMENT_UNVERIFIED" in unsafe_bool_response
    assert "제공된 파괴성 인자가 카탈로그 기반 fail-safe 요청과 달라" in (
        unsafe_bool_response
    )
    assert "파괴적 BOOL 인자의 fail-safe 값을 증명하지 못해" not in (
        unsafe_bool_response
    )
    assert "OWNED_ISOLATION_REQUIRED" in fail_safe_bool_response
    assert len(dispatcher.calls) == 3
