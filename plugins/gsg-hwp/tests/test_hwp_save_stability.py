from __future__ import annotations

import sys
from base64 import b64encode
from pathlib import Path
from typing import ClassVar

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

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_batch_contract import (  # noqa: E402
    decode_lifecycle_result,
    decode_save_result,
)
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_session_lifecycle import lifecycle_result, save_result  # noqa: E402
from hwp_mcp import build_server  # noqa: E402


class _InputSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    required: tuple[str, ...] = ()
    properties: dict[str, JsonValue]


def _encoded(value: str) -> str:
    return b64encode(value.encode("utf-8")).decode("ascii")


def _save_payload(*, verified: bool = True, after_document_hash: str = "103") -> str:
    return "\t".join(
        (
            "HLS1",
            "1" if verified else "0",
            _encoded("C:/docs/sample.hwp"),
            "3",
            "1",
            "2",
            "101",
            "102",
            "103",
            "0",
            "1",
            "0",
            "3",
            "0",
            "2",
            "101",
            "102",
            after_document_hash,
            "4096",
            "250",
        )
    )


def test_general_save_decodes_full_content_readback() -> None:
    native = decode_save_result(_save_payload())
    result = save_result("문서 일반 저장", native)

    assert native.verified is True
    assert native.before_text_hash == native.after_text_hash == "102"
    assert native.before_document_hash == native.after_document_hash == "103"
    assert result.status == "executed"
    assert result.verified is True
    assert result.verification == "native_save_result"
    assert result.saved_path == "C:/docs/sample.hwp"


def test_general_save_rejects_claimed_success_when_document_hash_changed() -> None:
    with pytest.raises(HwpLiveError, match="readback 근거"):
        _ = decode_save_result(_save_payload(after_document_hash="999"))


def test_reopen_failure_reports_recovered_document_session() -> None:
    payload = "\t".join(
        (
            "HCL12",
            "0",
            "",
            "3",
            "1",
            "2",
            "101",
            "102",
            "103",
            "0",
            "1",
            "0",
            "0",
            "-1",
            "0",
            "0",
            "1",
            "0",
            "1",
            "3",
            "1",
            "2",
            "101",
            "102",
            "103",
            "300",
        )
    )
    native = decode_lifecycle_result(payload)
    result = lifecycle_result("문서 저장 재개방 검증", native)

    assert native.recovered is True
    assert result.status == "operation_failed"
    assert result.verified is False
    assert result.session_recovered is True
    assert result.partial_mutation is False
    assert result.retry_safe is True


def test_production_exposes_non_destructive_save_separately() -> None:
    server = build_server(LiveHwpController(), profile="production")
    tools = anyio.run(server.list_tools)
    schemas = {
        tool.name: _InputSchema.model_validate(tool.inputSchema) for tool in tools
    }

    assert "hwp_save" in schemas
    assert schemas["hwp_save"].required == ("operation_id",)
    assert frozenset(schemas["hwp_save"].properties) == frozenset(
        ("operation_id", "document_path")
    )
    assert "hwp_save_reopen_verify" in schemas
