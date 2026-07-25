from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from pathlib import Path
from typing import ClassVar, cast, final
from unittest.mock import patch

import anyio
import pytest
from pydantic import BaseModel, ConfigDict, JsonValue


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_native_action_contract import encode_action_request  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    IntegerValue,
    NativeActionResult,
    NativeActionRequest,
    NativeCharacterFormat,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
    ParameterActionCommand,
    TextPatchCommand,
)
from hwp_live_api import HwpComApplication, HwpComDocument, LiveHwpApplication  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_session_structure_mutation import patch_validated_text  # noqa: E402
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_mcp import build_server  # noqa: E402
from hwp_mcp_operation_executor import _text_match_candidates  # noqa: E402
from hwp_operation_contract import OperationResult  # noqa: E402
from hwp_public_action_contract import (  # noqa: E402
    PublicTextPatchPosition,
    PublicTextPatchTarget,
    PublicTextFormattingInput,
)
from hwp_public_contract import to_public_action_result  # noqa: E402
from hwp_live_text_patch_contract import TextPatchRequest, TextPatchTarget  # noqa: E402


class _InputSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    required: tuple[str, ...] = ()
    properties: dict[str, JsonValue]


def _request(command: TextPatchCommand) -> NativeActionRequest:
    return NativeActionRequest(
        document_id=17,
        full_name="C:/documents/sample.hwp",
        commands=(command,),
    )


@final
class _Document:
    DocumentID = 17
    FullName = "C:/documents/sample.hwp"


def _snapshot(
    *,
    selected: bool,
    selected_text: str,
    start: NativePosition,
    end: NativePosition,
    text_color: int,
) -> NativeSnapshot:
    return NativeSnapshot(
        document_id=17,
        full_name="C:/documents/sample.hwp",
        current_page=1,
        page_count=1,
        modified=True,
        cursor=end,
        selection=NativeSelection(selected, start, end, 1 if selected else 0),
        selected_text=selected_text,
        control_type="",
        control_instance_id="",
        cell_address="",
        style_id=0,
        character_format=NativeCharacterFormat("맑은 고딕", 1_000, False, text_color),
        paragraph_format=NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0),
    )


def _candidate() -> HwpDocumentCandidate:
    return HwpDocumentCandidate(
        selector="active",
        moniker_name="fixture",
        application=cast(HwpComApplication, object()),
        document=cast(HwpComDocument, cast(object, _Document())),
        document_id=17,
        full_name="C:/documents/sample.hwp",
        document_format="HWP",
        edit_mode=1,
        window_handle=100,
        active=True,
    )


def test_patch_current_command_encodes_selection_free_insert() -> None:
    # Given a patch request at a collapsed cursor with no expected old text
    command = TextPatchCommand(
        target="current",
        expected_text=None,
        replacement="새 문장",
    )

    # When the request is encoded for the native add-in
    payload = encode_action_request(_request(command))

    # Then one PATCH_TEXT command carries the explicit current-target contract
    assert "PATCH_TEXT\tCURRENT\t0\t \t7IOIIOusuOyepQ==\n" in payload
    assert "DeletePage" not in payload
    assert "ApplyLayout" not in payload
    assert "ReferenceLayoutBulk" not in payload


def test_patch_range_command_encodes_exact_verified_range() -> None:
    # Given an explicit range and the text that must still occupy it
    command = TextPatchCommand(
        target="range",
        expected_text="기존",
        replacement="변경",
        start=NativePosition(0, 7, 3),
        end=NativePosition(0, 7, 5),
    )

    # When the command is encoded
    payload = encode_action_request(_request(command))

    # Then both native endpoints and both text values are present
    assert "PATCH_TEXT\tRANGE\t0\t7\t3\t0\t7\t5\t6riw7KG0\t67OA6rK9\n" in payload
    assert "DeletePage" not in payload
    assert "ApplyLayout" not in payload


def test_public_patch_target_rejects_incomplete_or_mixed_coordinates() -> None:
    with pytest.raises(ValueError):
        _ = PublicTextPatchTarget(
            kind="range",
            start=PublicTextPatchPosition(list_id=0, paragraph=1, character=0),
        )

    with pytest.raises(ValueError):
        _ = PublicTextPatchTarget(
            kind="find",
            cell="A1",
        )


def test_ambiguous_text_patch_returns_exact_candidates() -> None:
    candidates = _text_match_candidates(
        "0:2:1-0:2:3|4:7:5-4:7:7",
        "기존",
    )

    assert tuple(candidate.occurrence for candidate in candidates) == (1, 2)
    assert candidates[0].start.list_id == 0
    assert candidates[0].start.paragraph == 2
    assert candidates[0].start.character == 1
    assert candidates[1].end.list_id == 4
    assert candidates[1].end.paragraph == 7
    assert candidates[1].end.character == 7
    assert all(candidate.matched_text == "기존" for candidate in candidates)


def test_public_ambiguous_text_patch_preserves_candidates_and_retry_input() -> None:
    candidates = _text_match_candidates(
        "0:2:1-0:2:3|4:7:5-4:7:7",
        "기존",
    )
    result = OperationResult(
        status="ambiguous",
        query="text.patch",
        registry_entries=1,
        lookup_microseconds=0,
        text_candidates=candidates,
        message="검색 결과가 여러 개입니다",
        modified=False,
        commands_executed=0,
        retry_safe=True,
    )

    public = to_public_action_result(result, ())

    assert public.status == "needs_target"
    assert "occurrence=1 start=0:2:1 end=0:2:3" in public.message
    assert "occurrence=2 start=4:7:5 end=4:7:7" in public.message
    assert "hwp_get_operation_status" in public.message
    assert public.required_inputs == ("target.occurrence",)
    assert public.modified is False
    assert public.commands_executed == 0
    assert public.retry_safe is True


def test_production_exposes_one_atomic_text_patch_tool() -> None:
    # Given the production MCP surface
    server = build_server(LiveHwpController(), profile="production")

    # When its public schemas are listed
    tools = anyio.run(server.list_tools)
    schemas = {
        tool.name: _InputSchema.model_validate(tool.inputSchema) for tool in tools
    }

    # Then text.patch accepts target, expected text, replacement, and formatting together
    schema = schemas["hwp_patch_text"]
    assert schema.required == ("operation_id", "replacement")
    assert frozenset(schema.properties) == frozenset(
        (
            "operation_id",
            "replacement",
            "target",
            "expected_text",
            "formatting",
            "document_path",
        )
    )


def test_patch_and_red_format_execute_in_one_native_batch_with_readback() -> None:
    start = NativePosition(0, 3, 2)
    before = _snapshot(
        selected=False,
        selected_text="",
        start=start,
        end=start,
        text_color=0,
    )
    end = NativePosition(0, 3, 6)
    after = _snapshot(
        selected=True,
        selected_text="변경",
        start=start,
        end=end,
        text_color=255,
    )
    native_result = NativeActionResult(2, 1, 1, 0, 50, ())
    captured: list[NativeActionRequest] = []

    def execute(
        _window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        assert minimum_version == 11
        captured.append(request)
        return native_result

    request = TextPatchRequest(
        target=TextPatchTarget(kind="current"),
        expected_text=None,
        replacement="변경",
        formatting=PublicTextFormattingInput(text_color="빨간색").to_live(),
    )
    with (
        patch(
            "hwp_live_session_structure_mutation.read_native_snapshot",
            side_effect=(before, after),
        ),
        patch(
            "hwp_live_session_structure_mutation.execute_native_actions",
            side_effect=execute,
        ),
    ):
        result = patch_validated_text(
            cast(LiveHwpApplication, object()),
            _candidate(),
            request,
            set(),
            lambda: None,
        )

    assert result.after.selected_text == "변경"
    assert len(captured) == 1
    assert isinstance(captured[0].commands[0], TextPatchCommand)
    character = captured[0].commands[1]
    assert isinstance(character, ParameterActionCommand)
    assert character.action == "CharShape"
    assert any(
        setter.path == "TextColor"
        and isinstance(setter.value, IntegerValue)
        and setter.value.value == 255
        for setter in character.setters
    )


def test_patch_rejects_false_success_when_reselected_text_differs() -> None:
    start = NativePosition(0, 3, 2)
    before = _snapshot(
        selected=False,
        selected_text="",
        start=start,
        end=start,
        text_color=0,
    )
    after = _snapshot(
        selected=True,
        selected_text="다른 값",
        start=start,
        end=NativePosition(0, 3, 6),
        text_color=255,
    )
    request = TextPatchRequest(
        target=TextPatchTarget(kind="current"),
        expected_text=None,
        replacement="변경",
    )

    with (
        patch(
            "hwp_live_session_structure_mutation.read_native_snapshot",
            side_effect=(before, after),
        ),
        patch(
            "hwp_live_session_structure_mutation.execute_native_actions",
            return_value=NativeActionResult(1, 0, 1, 0, 50, ()),
        ),
        pytest.raises(HwpLiveError, match="변경한 본문 범위"),
    ):
        _ = patch_validated_text(
            cast(LiveHwpApplication, object()),
            _candidate(),
            request,
            set(),
            lambda: None,
        )
