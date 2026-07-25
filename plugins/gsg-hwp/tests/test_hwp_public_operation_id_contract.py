from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import anyio
from pydantic import BaseModel, ConfigDict, JsonValue


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_mcp import build_server  # noqa: E402
from hwp_mcp_forward import HwpExecuteArguments  # noqa: E402
from hwp_mcp_operation_executor import HwpOperationExecutor  # noqa: E402
from hwp_mcp_registry import tool_specs  # noqa: E402
from hwp_operation_contract import (  # noqa: E402
    HwpOperateGuards,
    HwpOperateInputs,
    OperationResult,
)


class _InputSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    required: tuple[str, ...] = ()
    properties: dict[str, JsonValue]


def test_all_production_writes_require_caller_operation_id() -> None:
    server = build_server(LiveHwpController(), profile="production")
    schemas = {
        tool.name: _InputSchema.model_validate(tool.inputSchema)
        for tool in anyio.run(server.list_tools)
    }
    write_names = tuple(
        spec.name
        for spec in tool_specs("production")
        if spec.operation == "write" and spec.name != "hwp_execute"
    )

    assert len(write_names) == 26
    assert all("operation_id" in schemas[name].properties for name in write_names)
    assert all("operation_id" in schemas[name].required for name in write_names)
    assert schemas["hwp_get_operation_status"].required == ("operation_id",)


def test_hwp_execute_preserves_caller_operation_id() -> None:
    operation_id = "forwarded-stable-operation"
    captured: list[str | None] = []

    async def capture_execute(
        _executor: HwpOperationExecutor,
        intent: str,
        inputs: HwpOperateInputs,
        _guards: HwpOperateGuards | None,
    ) -> OperationResult:
        captured.append(inputs.request_id)
        return OperationResult(
            request_id=inputs.request_id,
            status="executed",
            changed=True,
            query=intent,
            registry_entries=1,
            lookup_microseconds=0,
            message="ok",
        )

    async def exercise_forwarder() -> None:
        with patch.object(HwpOperationExecutor, "execute", capture_execute):
            server = build_server(LiveHwpController(), profile="production")
            _ = await server.call_unconverted_tool(
                "hwp_execute",
                HwpExecuteArguments(
                    root={
                        "tool_name": "hwp_delete_page",
                        "arguments": {
                            "operation_id": operation_id,
                            "page": 2,
                        },
                    }
                ),
            )

    anyio.run(exercise_forwarder)

    assert captured == [operation_id]
