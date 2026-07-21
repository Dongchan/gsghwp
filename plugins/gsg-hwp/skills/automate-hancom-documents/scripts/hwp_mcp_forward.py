from __future__ import annotations

from typing import Final, final

from mcp.server.fastmcp import FastMCP
from pydantic import JsonValue, RootModel, TypeAdapter
from pydantic_core import to_json

from hwp_runtime_identity import RuntimeStatus, runtime_status


_JSON_VALUE: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)


class HwpExecuteArguments(RootModel[dict[str, JsonValue]]):
    pass


@final
class ForwardingFastMCP(FastMCP[None]):
    async def hwp_runtime_info(self) -> RuntimeStatus:
        return runtime_status(await self.list_tools())

    async def call_unconverted_tool(
        self,
        name: str,
        arguments: HwpExecuteArguments,
    ) -> JsonValue:
        serialized: bytes = to_json(
            await self._tool_manager.call_tool(
                name,
                arguments.root,
                context=self.get_context(),
                convert_result=False,
            )
        )
        return _JSON_VALUE.validate_json(serialized)
