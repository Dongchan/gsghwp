from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import ClassVar, cast, get_type_hints

from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_mcp_wrappers  # noqa: E402
from hwp_live_contract import (  # noqa: E402
    DocumentStyleList,
    LiveContext,
    PreviewResult,
)
from hwp_live_structure_contract import (  # noqa: E402
    DocumentStructure,
    FastPageInspection,
)
from hwp_mcp_document_wrappers import McpDocumentRecipeWrappers  # noqa: E402
from hwp_operation_contract import OperationResult  # noqa: E402
from hwp_public_inspection_tools import HwpPublicInspectionTools  # noqa: E402


class _LargePayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: str
    body: str


def _bytes(value: str) -> int:
    return len(value.encode("utf-8"))


def test_compact_result_keeps_structured_payload_without_text_copy() -> None:
    # Given
    marker = "구조화-전용-표식-" * 5_000
    payload = _LargePayload(status="ok", body=marker)

    # When
    assert hasattr(hwp_mcp_wrappers, "compact_structured_result")
    result = cast(
        CallToolResult,
        cast(
            object,
            hwp_mcp_wrappers.compact_structured_result(
                payload,
                summary="structured result available",
            ),
        ),
    )

    # Then
    assert result.structuredContent == payload.model_dump(mode="json")
    text = "".join(
        block.text for block in result.content if isinstance(block, TextContent)
    )
    assert marker not in text
    structured_json = json.dumps(
        result.structuredContent,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    result_json = result.model_dump_json(
        by_alias=True,
        exclude_none=True,
    )
    assert _bytes(result_json) <= _bytes(structured_json) * 1.2


def test_owned_tools_keep_output_schema_while_returning_call_tool_result() -> None:
    # Given
    expected = (
        (HwpPublicInspectionTools.hwp_inspect, LiveContext),
        (HwpPublicInspectionTools.hwp_list_styles, DocumentStyleList),
        (HwpPublicInspectionTools.hwp_inspect_page_fast, FastPageInspection),
        (HwpPublicInspectionTools.hwp_render_page, PreviewResult),
        (HwpPublicInspectionTools.hwp_inspect_structure, DocumentStructure),
        (McpDocumentRecipeWrappers.hwp_copy_style, OperationResult),
    )

    # When / Then
    for handler, output_model in expected:
        return_type = cast(
            object,
            get_type_hints(handler, include_extras=True)["return"],
        )
        assert return_type is output_model
