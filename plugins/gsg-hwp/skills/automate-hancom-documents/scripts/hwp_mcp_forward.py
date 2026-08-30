from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Final, final, override

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ContentBlock, Tool
from pydantic import JsonValue, RootModel, TypeAdapter, ValidationError
from pydantic_core import to_json

from hwp_live_native_format_inputs import (
    normalize_border_width_input,
    normalize_format_name_input,
)
from hwp_runtime_identity import RuntimeStatus, runtime_status


_JSON_VALUE: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)
_BOOLEAN_VALUE: Final[TypeAdapter[bool]] = TypeAdapter(bool)
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
_DOCUMENT_SELECTOR_DESCRIPTION: Final = (
    "document_path 별칭: 전체 경로, document_id:<ID>, 열린 문서 selector."
)
# 표 대상 도구는 문서 선택자를 target.document_path 안쪽에 두고 있었다. 같은 값을
# 최상위에 실어 보내면 FastMCP 가 모르는 인자로 조용히 버렸고, 편집은 그 문서가
# 아니라 그때 활성인 문서로 갔다. 최상위 이름을 다른 도구와 똑같이 공개하고,
# 호출 때 target.document_path 로 옮겨 싣는다.
_TARGET_DOCUMENT_DESCRIPTION: Final = (
    "편집할 문서: 전체 경로, document_id:<ID>, 열린 문서 selector. "
    "이 도구는 같은 값을 target.document_path로 옮겨 실행합니다."
)
# hwp_connect 는 selector, hwp_open_document 는 reference_selector 로 부르지만
# 둘 다 select_operation_document 로 문서를 고르고, 그 실패 문구는
# "document_selector와 일치하는 편집 문서를 찾을 수 없습니다" 다. 문구가 가르친
# 이름을 도구가 안 받아서 FastMCP 가 조용히 버렸고, 세션은 그때 활성인 문서에
# 묶였다. 여기서 같은 이름을 받아 그 도구의 실제 인자로 옮겨 싣는다.
_ALIAS_SELECTOR_DESCRIPTION: Final = (
    "{name} 별칭: 전체 경로, document_id:<ID>, 열린 문서 selector. "
    "이 도구는 같은 값을 {name}에 실어 실행합니다."
)
_ALIAS_SELECTOR_SUFFIX: Final = "_selector"
_PUBLIC_TEXT_TARGET_TOOLS: Final = frozenset(
    ("hwp_format_text", "hwp_patch_text", "hwp_replace_selected_text")
)
_PUBLIC_TEXT_SEARCH_KEYS: Final = frozenset(
    (
        "find",
        "find_text",
        "old_text",
        "pattern",
        "query",
        "search",
        "search_text",
        "expected_text",
    )
)
_PUBLIC_TEXT_TARGET_ALIASES: Final = {
    "address": "cell",
    "case_sensitive": "match_case",
    "cell_address": "cell",
    "cell_name": "cell",
    "control_instance_id": "table_instance_id",
    "index": "occurrence",
    "instance_id": "table_instance_id",
    "nth": "occurrence",
    "table_id": "table_instance_id",
    "target_id": "table_instance_id",
}
_PUBLIC_FORMAT_NAME_FIELDS: Final = {
    "hwp_fill_table": ("numeric_value_mode", "scale_conflict"),
    "hwp_format_text": ("alignment",),
    "hwp_format_table": (
        "alignment",
        "vertical_alignment",
        "border_style",
    ),
    "hwp_insert_image": ("fit", "target"),
    "hwp_split_table_cell": ("split_mode",),
}
_PUBLIC_CELL_ADDRESS_FIELDS: Final = {
    "hwp_format_table": ("cell",),
    "hwp_merge_table_cells": ("start_cell", "end_cell"),
    "hwp_split_table_cell": ("cell",),
    "hwp_edit_picture": ("cell", "move_to_cell"),
}
# An A1-style address and nothing else. The same fields also accept a selector
# object, which FastMCP allows a caller to send as a JSON string, so the
# padding-only normalisation above must be able to tell the two apart before
# that string is parsed. Case is left to ``PublicCellSelector.normalize_address``
# because ``PublicCellAddress`` already accepts either case.
_CELL_ADDRESS_TEXT: Final = re.compile(r"\s*[A-Za-z]+[1-9][0-9]*\s*")
_STRICT_PUBLIC_INPUTS: Final = frozenset(
    (
        "hwp_ground_document",
        "hwp_compile_page_plan",
        "hwp_apply_page_plan",
        "hwp_get_graph_manifest",
        "hwp_query_graph",
        "hwp_get_graph_node",
        "hwp_get_graph_property",
        "hwp_get_graph_asset",
        "hwp_fetch_graph_artifact",
        "hwp_list_custom_actions",
        "hwp_get_custom_action",
        "hwp_register_custom_action",
        "hwp_update_custom_action",
        "hwp_delete_custom_action",
        "hwp_reorder_custom_actions",
        "hwp_remove_custom_action_tab",
    )
)
_STRICT_PUBLIC_INPUT_FIELDS: Final = {
    "hwp_ground_document": frozenset(
        (
            "pages",
            "dpi",
            "include_overlay",
            "document_path",
            "document_selector",
        )
    ),
    "hwp_compile_page_plan": frozenset(("request",)),
    "hwp_apply_page_plan": frozenset(("request",)),
    "hwp_get_graph_manifest": frozenset(
        ("document_id", "store_id", "projection_bits", "expected_version")
    ),
    "hwp_query_graph": frozenset(
        (
            "document_id",
            "store_id",
            "projection_bits",
            "kinds",
            "page_size",
            "continuation",
            "expected_version",
        )
    ),
    "hwp_get_graph_node": frozenset(
        ("document_id", "store_id", "node_id", "projection_bits", "expected_version")
    ),
    "hwp_get_graph_property": frozenset(
        (
            "document_id",
            "store_id",
            "node_id",
            "property_key",
            "occurrence",
            "projection_bits",
            "expected_version",
        )
    ),
    "hwp_get_graph_asset": frozenset(
        ("document_id", "store_id", "record_id", "projection_bits", "expected_version")
    ),
    "hwp_fetch_graph_artifact": frozenset(
        (
            "uri",
            "document_id",
            "store_id",
            "expected_version",
            "offset",
            "limit",
        )
    ),
    "hwp_list_custom_actions": frozenset(("include_tab_state", "process_id")),
    "hwp_get_custom_action": frozenset(("action_id",)),
    "hwp_register_custom_action": frozenset(
        ("action_id", "label", "steps", "description", "process_id")
    ),
    "hwp_update_custom_action": frozenset(
        ("action_id", "label", "description", "steps", "process_id")
    ),
    "hwp_delete_custom_action": frozenset(("action_id", "process_id")),
    "hwp_reorder_custom_actions": frozenset(("action_ids", "process_id")),
    "hwp_remove_custom_action_tab": frozenset(("purge_registry", "process_id")),
}


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


def _resolved_schema(
    root: dict[str, JsonValue],
    node: JsonValue,
    depth: int = 0,
) -> dict[str, JsonValue] | None:
    if not isinstance(node, dict) or depth > 8:
        return None
    reference = node.get("$ref")
    if isinstance(reference, str):
        definitions = root.get("$defs")
        if not isinstance(definitions, dict):
            return None
        name = reference.rsplit("/", maxsplit=1)[-1]
        return _resolved_schema(root, definitions.get(name), depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        branches = node.get(key)
        if not isinstance(branches, list):
            continue
        for branch in branches:
            resolved = _resolved_schema(root, branch, depth + 1)
            if resolved is not None and "properties" in resolved:
                return resolved
    return node


def _target_document_property(
    schema: dict[str, JsonValue],
) -> dict[str, JsonValue] | None:
    """The ``document_path`` a tool hides inside its ``target`` object.

    None when the tool already publishes ``document_path`` at the top level, or
    when its ``target`` carries no document selector at all. Only the seven
    table tools (format/merge/split/expand/fill images/repeat/series) answer.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict) or "document_path" in properties:
        return None
    resolved = _resolved_schema(schema, properties.get("target"))
    if resolved is None:
        return None
    target_properties = resolved.get("properties")
    if not isinstance(target_properties, dict):
        return None
    document_path = target_properties.get("document_path")
    return document_path if isinstance(document_path, dict) else None


def _aliased_selector_property(schema: dict[str, JsonValue]) -> str | None:
    """The document selector a tool publishes under a name of its own.

    Only ``hwp_connect`` (``selector``) and ``hwp_open_document``
    (``reference_selector``) answer in either profile. ``None`` when the tool
    already publishes ``document_path``/``document_selector``, when it has no
    such name, or when two names could be meant -- guessing which argument is
    the document is the mistake this exists to stop, so an ambiguous tool is
    left exactly as it was.

    Say what "left exactly as it was" costs: a top-level ``document_selector``
    sent to a tool this returns ``None`` for is dropped without a word, the
    behaviour every other part of this file exists to end. That is tolerable
    only while no tool is ambiguous, which is why
    ``test_only_two_tools_name_their_own_document_selector`` walks both full
    catalogs and fails the moment a third name appears. A tool that reaches
    that state needs a refusal here, not a silent drop.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    if "document_path" in properties or "document_selector" in properties:
        return None
    named = tuple(
        name
        for name in properties
        if name == "selector" or name.endswith(_ALIAS_SELECTOR_SUFFIX)
    )
    return named[0] if len(named) == 1 else None


def _document_selector_schema(tool: Tool) -> Tool:
    schema = _JSON_VALUE.validate_python(tool.inputSchema)
    if not isinstance(schema, dict):
        return tool
    updated_schema = schema
    properties = schema.get("properties")
    if isinstance(properties, dict):
        document_path = properties.get("document_path")
        updated_properties: dict[str, JsonValue] | None = None
        if isinstance(document_path, dict):
            alias_schema = dict(document_path)
            alias_schema["description"] = _DOCUMENT_SELECTOR_DESCRIPTION
            updated_properties = dict(properties)
            updated_properties["document_selector"] = alias_schema
        else:
            nested = _target_document_property(schema)
            alias = None if nested is not None else _aliased_selector_property(schema)
            aliased = None if alias is None else properties.get(alias)
            if nested is not None:
                forwarded = dict(nested)
                forwarded["description"] = _TARGET_DOCUMENT_DESCRIPTION
                updated_properties = dict(properties)
                updated_properties["document_path"] = forwarded
                updated_properties["document_selector"] = dict(forwarded)
            elif alias is not None and isinstance(aliased, dict):
                # document_selector 만 낸다. hwp_open_document 는 이미 path 를
                # "열 파일" 로 쓰고 있어서 document_path 를 같이 내면 열 파일이
                # 참조 문서 자리로 갈 수 있다. 호출 때는 두 이름을 모두 받는다.
                forwarded = dict(aliased)
                forwarded["description"] = _ALIAS_SELECTOR_DESCRIPTION.format(
                    name=alias
                )
                updated_properties = dict(properties)
                updated_properties["document_selector"] = forwarded
        if updated_properties is not None:
            updated_schema = dict(schema)
            updated_schema["properties"] = updated_properties
        if tool.name in _STRICT_PUBLIC_INPUTS:
            updated_schema = dict(updated_schema)
            updated_schema["additionalProperties"] = False
        if tool.name in {"hwp_get_graph_node", "hwp_get_graph_property"}:
            graph_properties = updated_schema.get("properties")
            if isinstance(graph_properties, dict):
                node_id = graph_properties.get("node_id")
                if isinstance(node_id, dict):
                    updated_node_id = dict(node_id)
                    updated_node_id["pattern"] = "^[0-9a-f]{32}$"
                    updated_properties = dict(graph_properties)
                    updated_properties["node_id"] = updated_node_id
                    updated_schema = dict(updated_schema)
                    updated_schema["properties"] = updated_properties
    updated_tool = tool.model_copy(
        update={"inputSchema": _without_schema_titles(updated_schema)}
    )
    if (
        tool.name in {"hwp_compile_page_plan", "hwp_apply_page_plan"}
        and tool.outputSchema is not None
    ):
        output_schema = _JSON_VALUE.validate_python(tool.outputSchema)
        if isinstance(output_schema, dict):
            strict_output_schema = dict(output_schema)
            strict_output_schema["additionalProperties"] = False
            updated_tool = updated_tool.model_copy(
                update={"outputSchema": _without_schema_titles(strict_output_schema)}
            )
    return updated_tool


def _single_document_value(
    first: JsonValue,
    second: JsonValue,
    message: str,
) -> JsonValue:
    if first is None:
        return second
    if second is None or first == second:
        return first
    raise ToolError(message)


def _target_object(value: JsonValue) -> dict[str, JsonValue] | None:
    """A ``target`` that arrived as a JSON string, read as the object it is.

    FastMCP keeps ``func_metadata.pre_parse_json`` because callers do send
    objects as strings, but that runs *after* this normalization, so a string
    ``target`` reached here unparsed and the top-level selector had nowhere to
    go. ``None`` when the string is not an object -- the caller then hears
    about it instead of losing the selector.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = _JSON_VALUE.validate_json(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalized_boolean(value: JsonValue) -> JsonValue:
    try:
        return _BOOLEAN_VALUE.validate_python(value)
    except ValidationError:
        return value


def _require_search_text(value: JsonValue) -> JsonValue:
    if value == "":
        raise ToolError(
            "빈 검색 문자열로는 고칠 위치를 정할 수 없습니다. "
            + "최상위 expected_text에 찾을 문자열을 1자 이상 넣으세요"
        )
    return value


def _normalize_public_text_target(
    name: str,
    arguments: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    if name not in _PUBLIC_TEXT_TARGET_TOOLS:
        return arguments
    normalized = dict(arguments)
    if "expected_text" in normalized:
        normalized["expected_text"] = _require_search_text(normalized["expected_text"])
    supplied = arguments.get("target")
    target = supplied if isinstance(supplied, dict) else _target_object(supplied)
    if target is None:
        return normalized
    updated_target = dict(target)
    nested_expected: JsonValue = None
    for key in _PUBLIC_TEXT_SEARCH_KEYS:
        if key not in updated_target:
            continue
        supplied_expected = updated_target.pop(key)
        if supplied_expected is None:
            continue
        nested_expected = _single_document_value(
            nested_expected,
            _require_search_text(supplied_expected),
            "target 안의 검색 문자열 값들이 서로 다릅니다",
        )
    expected_text = _single_document_value(
        normalized.get("expected_text"),
        nested_expected,
        "expected_text와 target 안의 검색 문자열 값이 서로 다릅니다",
    )
    if expected_text is not None:
        normalized["expected_text"] = expected_text
    supplied_kind = updated_target.get("kind")
    if isinstance(supplied_kind, str):
        supplied_kind = normalize_format_name_input(supplied_kind)
        updated_target["kind"] = supplied_kind
    if (
        nested_expected is not None
        and supplied_kind is not None
        and supplied_kind != "find"
    ):
        raise ToolError(
            f'target.kind="{supplied_kind}"는 target 안의 검색 문자열과 모순됩니다. '
            + '검색 문자열을 승격하려면 target.kind="find"를 사용하세요'
        )
    for alias, canonical in _PUBLIC_TEXT_TARGET_ALIASES.items():
        if alias not in updated_target:
            continue
        alias_value = updated_target.pop(alias)
        if alias_value is None:
            continue
        updated_target[canonical] = _single_document_value(
            updated_target.get(canonical),
            alias_value,
            f"target.{canonical}과 target.{alias} 값이 서로 다릅니다",
        )
    if "match_case" in updated_target:
        updated_target["match_case"] = _normalized_boolean(updated_target["match_case"])
    if "kind" not in updated_target:
        if (
            nested_expected is not None
            or updated_target.get("occurrence") is not None
            or updated_target.get("match_case") is True
        ):
            updated_target["kind"] = "find"
        elif (
            updated_target.get("start") is not None
            or updated_target.get("end") is not None
        ):
            updated_target["kind"] = "range"
        elif (
            updated_target.get("table_instance_id") is not None
            or updated_target.get("cell") is not None
        ):
            updated_target["kind"] = "table_cell"
    normalized["target"] = updated_target
    return normalized


def _normalize_public_format_arguments(
    name: str,
    arguments: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    fields = _PUBLIC_FORMAT_NAME_FIELDS.get(name)
    cell_fields = _PUBLIC_CELL_ADDRESS_FIELDS.get(name)
    normalize_width = name == "hwp_format_table" and "border_width" in arguments
    if fields is None and cell_fields is None and not normalize_width:
        return arguments
    normalized = dict(arguments)
    for field in fields or ():
        if field not in normalized:
            continue
        raw_value = normalized[field]
        if not isinstance(raw_value, (str, int, float, bool)) and raw_value is not None:
            continue
        value = normalize_format_name_input(raw_value)
        if value is not None:
            normalized[field] = value
    for field in cell_fields or ():
        raw_value = normalized.get(field)
        # These fields are ``PublicCellAddress | PublicCellSelector``, and
        # FastMCP lets a caller send the selector object as a JSON string. This
        # runs before that string is parsed, so anything that is not plainly an
        # A1-style address has to be left exactly as it arrived -- uppercasing
        # a selector rewrites its keys and its label text.
        if isinstance(raw_value, str) and _CELL_ADDRESS_TEXT.fullmatch(raw_value):
            normalized[field] = raw_value.strip()
    if normalize_width:
        raw_width = normalized["border_width"]
        if isinstance(raw_width, (str, int, float, bool)) or raw_width is None:
            width = normalize_border_width_input(raw_width)
            if width is not None:
                normalized["border_width"] = width
    return normalized


def _document_selector_into_target(
    arguments: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Carry a top-level document selector into ``target.document_path``.

    Without this the value was dropped without a word and the edit went to
    whatever document happened to be active. ``target.document_selector`` is
    accepted here too, so the same spelling works at either level.
    """
    normalized = dict(arguments)
    top = _single_document_value(
        normalized.pop("document_path", None),
        normalized.pop("document_selector", None),
        "document_path와 document_selector에 서로 다른 값을 지정할 수 없습니다",
    )
    supplied = normalized.get("target")
    target = supplied if isinstance(supplied, dict) else _target_object(supplied)
    if target is None:
        if top is None:
            return normalized
        if supplied is None:
            normalized["target"] = {"document_path": top}
            return normalized
        # 실을 자리가 없다. 조용히 버리면 편집이 그때 활성인 문서로 간다.
        raise ToolError(
            "target을 객체로 전달하세요. 객체가 아닌 target에는 "
            + "document_selector를 실을 수 없습니다"
        )
    updated_target = dict(target)
    nested = _single_document_value(
        updated_target.get("document_path"),
        updated_target.pop("document_selector", None),
        "target.document_path와 target.document_selector에 "
        + "서로 다른 값을 지정할 수 없습니다",
    )
    merged = _single_document_value(
        top,
        nested,
        "document_selector와 target.document_path에 "
        + "서로 다른 값을 지정할 수 없습니다",
    )
    if merged is not None:
        updated_target["document_path"] = merged
    normalized["target"] = updated_target
    return normalized


def _document_selector_into_alias(
    arguments: dict[str, JsonValue],
    alias: str,
) -> dict[str, JsonValue]:
    """Carry a top-level document selector into the tool's own selector name.

    ``document_path`` is accepted at the call even though only
    ``document_selector`` is published, because every other document tool takes
    it and a value that is not carried anywhere is a value that binds the
    session to the wrong document.
    """
    normalized = dict(arguments)
    top = _single_document_value(
        normalized.pop("document_path", None),
        normalized.pop("document_selector", None),
        "document_path와 document_selector에 서로 다른 값을 지정할 수 없습니다",
    )
    if top is None:
        return normalized
    normalized[alias] = _single_document_value(
        normalized.get(alias),
        top,
        f"{alias}와 document_selector에 서로 다른 값을 지정할 수 없습니다",
    )
    return normalized


@final
class ForwardingFastMCP(FastMCP[None]):
    def _normalize_arguments(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return _normalize_public_format_arguments(
            name,
            _normalize_public_text_target(
                name,
                self._normalize_document_selector(name, arguments),
            ),
        )

    def _normalize_document_selector(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        tool = self._tool_manager.get_tool(name)
        if tool is None:
            return arguments
        properties = tool.parameters.get("properties")
        if not isinstance(properties, dict):
            return arguments
        if "document_path" not in properties:
            if _target_document_property(tool.parameters) is not None:
                return _document_selector_into_target(arguments)
            alias = _aliased_selector_property(tool.parameters)
            if alias is None:
                return arguments
            return _document_selector_into_alias(arguments, alias)
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

    def _reject_unknown_arguments(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> None:
        allowed = _STRICT_PUBLIC_INPUT_FIELDS.get(name)
        if allowed is None:
            return
        unknown = set(arguments) - allowed
        if unknown:
            raise ToolError(
                f"{name}에 알 수 없는 입력 필드가 있습니다: "
                + ", ".join(sorted(unknown))
            )

    @override
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> Sequence[ContentBlock] | dict[str, JsonValue]:
        self._reject_unknown_arguments(name, arguments)
        normalized = self._normalize_arguments(name, arguments)
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
        self._reject_unknown_arguments(name, arguments.root)
        normalized = self._normalize_arguments(name, arguments.root)
        serialized: bytes = to_json(
            await self._tool_manager.call_tool(
                name,
                normalized,
                context=self.get_context(),
                convert_result=False,
            )
        )
        return _JSON_VALUE.validate_json(serialized)
