from __future__ import annotations

import hashlib
import json

from mcp.types import Tool
from pydantic import JsonValue, TypeAdapter


_RUNTIME_BUILD_DEFAULT_KEYS = frozenset(
    ("distribution", "mcp", "native_bridge", "protocol")
)
_JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def normalize_runtime_build_defaults(
    value: JsonValue,
    *,
    distribution: str,
    canonical_distribution: str,
) -> JsonValue:
    """Normalize only a complete RuntimeBuildInfo default's distribution.

    Release packaging rewrites ``distribution`` while retaining ``source_version``.
    The complete four-field shape is required so unrelated defaults named
    ``distribution`` cannot be normalized accidentally. MCP, native bridge,
    protocol, types, required fields, descriptions, and every other schema byte
    remain unchanged.
    """
    if isinstance(value, list):
        return [
            normalize_runtime_build_defaults(
                item,
                distribution=distribution,
                canonical_distribution=canonical_distribution,
            )
            for item in value
        ]
    if not isinstance(value, dict):
        return value
    normalized = {
        key: normalize_runtime_build_defaults(
            item,
            distribution=distribution,
            canonical_distribution=canonical_distribution,
        )
        for key, item in value.items()
    }
    default = normalized.get("default")
    if (
        isinstance(default, dict)
        and frozenset(default) == _RUNTIME_BUILD_DEFAULT_KEYS
        and default.get("distribution") == distribution
        and isinstance(default.get("mcp"), str)
        and isinstance(default.get("native_bridge"), str)
        and isinstance(default.get("protocol"), int)
        and not isinstance(default.get("protocol"), bool)
    ):
        normalized["default"] = {
            **default,
            "distribution": canonical_distribution,
        }
    return normalized


def canonical_tool_schema_json(
    tool: Tool,
    *,
    distribution: str,
    canonical_distribution: str,
) -> str:
    payload = _JSON_VALUE.validate_python(
        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    normalized = normalize_runtime_build_defaults(
        payload,
        distribution=distribution,
        canonical_distribution=canonical_distribution,
    )
    return json.dumps(
        [normalized],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_catalog_schema_hash(
    tools: tuple[Tool, ...],
    *,
    distribution: str,
    canonical_distribution: str,
) -> str:
    payload = [
        json.loads(
            canonical_tool_schema_json(
                tool,
                distribution=distribution,
                canonical_distribution=canonical_distribution,
            )
        )[0]
        for tool in sorted(tools, key=lambda item: item.name)
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
