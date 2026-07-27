from __future__ import annotations

from collections.abc import Sequence
from typing import Final, final, override

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ContentBlock, Tool
from pydantic import JsonValue, RootModel, TypeAdapter
from pydantic_core import to_json

from hwp_runtime_identity import RuntimeStatus, runtime_status


_JSON_VALUE: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)
_SCHEMA_CHILD_KEYS: Final = frozenset(
    (
        "additionalItems",
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    )
)
_SCHEMA_CHILD_LIST_KEYS: Final = frozenset(("allOf", "anyOf", "oneOf", "prefixItems"))
_SCHEMA_CHILD_MAP_KEYS: Final = frozenset(
    ("$defs", "definitions", "dependentSchemas", "patternProperties", "properties")
)


class HwpExecuteArguments(RootModel[dict[str, JsonValue]]):
    pass


def _without_schema_titles(value: JsonValue) -> JsonValue:
    match value:  # noqa: E501  # noqa: MATCH_OK — JsonValue is exhaustively covered.
        case dict() as schema:
            compacted: dict[str, JsonValue] = {}
            for key, item in schema.items():
                if key == "title":
                    continue
                if key in _SCHEMA_CHILD_KEYS:
                    compacted[key] = _without_schema_titles(item)
                elif key in _SCHEMA_CHILD_LIST_KEYS and isinstance(item, list):
                    compacted[key] = [_without_schema_titles(child) for child in item]
                elif key in _SCHEMA_CHILD_MAP_KEYS and isinstance(item, dict):
                    compacted[key] = {
                        name: _without_schema_titles(child)
                        for name, child in item.items()
                    }
                else:
                    compacted[key] = item
            return compacted
        case list() as items:
            return [_without_schema_titles(item) for item in items]
        case str() | bool() | int() | float() | None:
            return value


def _document_selector_schema(tool: Tool) -> Tool:
    schema = _JSON_VALUE.validate_python(tool.inputSchema)
    if not isinstance(schema, dict):
        return tool
    updated_schema = schema
    properties = schema.get("properties")
    if isinstance(properties, dict):
        document_path = properties.get("document_path")
        if isinstance(document_path, dict):
            alias_schema = dict(document_path)
            alias_schema["description"] = (
                "document_path 별칭: 전체 경로, document_id:<ID>, 열린 문서 selector."
            )
            updated_properties = dict(properties)
            updated_properties["document_selector"] = alias_schema
            updated_schema = dict(schema)
            updated_schema["properties"] = updated_properties
    return tool.model_copy(
        update={"inputSchema": _without_schema_titles(updated_schema)}
    )


@final
class ForwardingFastMCP(FastMCP[None]):
    def _normalize_document_selector(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        tool = self._tool_manager.get_tool(name)
        if tool is None:
            return arguments
        properties = tool.parameters.get("properties")
        if not isinstance(properties, dict) or "document_path" not in properties:
            return arguments
        if "document_selector" not in arguments:
            return arguments
        document_path = arguments.get("document_path")
        document_selector = arguments["document_selector"]
        if (
            document_path is not None
            and document_selector is not None
            and document_path != document_selector
        ):
            raise ToolError(
                "document_path와 document_selector에 서로 다른 값을 지정할 수 없습니다"
            )
        normalized = dict(arguments)
        _ = normalized.pop("document_selector", None)
        if document_path is None:
            normalized["document_path"] = document_selector
        return normalized

    @override
    async def list_tools(self) -> list[Tool]:
        return [_document_selector_schema(tool) for tool in await super().list_tools()]

    @override
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> Sequence[ContentBlock] | dict[str, JsonValue]:
        normalized = self._normalize_document_selector(name, arguments)
        return await super().call_tool(
            name,
            normalized,
        )

    async def hwp_runtime_info(self) -> RuntimeStatus:
        return runtime_status(await self.list_tools())

    async def call_unconverted_tool(
        self,
        name: str,
        arguments: HwpExecuteArguments,
    ) -> JsonValue:
        normalized = self._normalize_document_selector(name, arguments.root)
        serialized: bytes = to_json(
            await self._tool_manager.call_tool(
                name,
                normalized,
                context=self.get_context(),
                convert_result=False,
            )
        )
        return _JSON_VALUE.validate_json(serialized)
