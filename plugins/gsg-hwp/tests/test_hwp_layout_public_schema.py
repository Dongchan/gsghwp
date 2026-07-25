from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

import anyio
from pydantic import JsonValue


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


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise AssertionError("schema node is not an object")
    return value


def _schema_ref_name(item: dict[str, JsonValue]) -> str:
    reference = item["$ref"]
    if not isinstance(reference, str):
        raise AssertionError("schema reference is not a string")
    return reference.rsplit("/", maxsplit=1)[-1]


def test_layout_tools_publish_the_same_discriminated_layout_plan_schema() -> None:
    server = build_server(LiveHwpController(), profile="production")
    tools = {tool.name: tool for tool in anyio.run(server.list_tools)}
    insert = cast(dict[str, JsonValue], tools["hwp_insert_layout"].inputSchema)
    append = cast(dict[str, JsonValue], tools["hwp_append_layout"].inputSchema)
    preflight = cast(dict[str, JsonValue], tools["hwp_preflight_layout"].inputSchema)

    insert_layout = _object(_object(insert["properties"])["layout"])
    append_layout = _object(_object(append["properties"])["layout"])
    preflight_layout = _object(_object(preflight["properties"])["layout"])
    assert insert_layout != {}
    assert insert_layout != {"type": "object"}
    assert append_layout == insert_layout
    assert preflight_layout == insert_layout

    layout_name = _schema_ref_name(insert_layout)
    definitions = _object(insert["$defs"])
    layout_plan = _object(definitions[layout_name])
    blocks = _object(_object(layout_plan["properties"])["blocks"])
    item_schema = _object(blocks["items"])
    discriminator = _object(item_schema["discriminator"])
    assert discriminator["propertyName"] == "kind"
    mapping_node = _object(discriminator["mapping"])
    mapping = {
        key: value for key, value in mapping_node.items() if isinstance(value, str)
    }
    assert set(mapping) == {
        "paragraph",
        "table",
        "image",
        "reference_layout",
        "reference_layout_patch",
        "page_break",
    }

    reference = _object(
        definitions[_schema_ref_name({"$ref": mapping["reference_layout"]})]
    )
    patch = _object(
        definitions[_schema_ref_name({"$ref": mapping["reference_layout_patch"]})]
    )
    assert {
        "row_breakpoints",
        "column_breakpoints",
        "merges",
        "visible_edges",
        "styles",
        "style_regions",
        "text_anchors",
    } <= _object(reference["properties"]).keys()
    assert {
        "target_control_id",
        "changed_rows",
        "changed_columns",
    } <= _object(patch["properties"]).keys()


def test_layout_schema_rejects_a_mismatched_discriminator_before_execution() -> None:
    server = build_server(LiveHwpController(), profile="production")

    async def invoke() -> object:
        return await server.call_unconverted_tool(
            "hwp_insert_layout",
            HwpExecuteArguments(
                {
                    "operation_id": "schema-rejects-invalid-block",
                    "layout": {
                        "target": "current",
                        "blocks": [
                            {
                                "kind": "reference_layout_patch",
                                "row_breakpoints": [0, 1],
                                "column_breakpoints": [0, 1],
                            }
                        ],
                    },
                }
            ),
        )

    try:
        _ = anyio.run(invoke)
    except Exception as error:
        assert "target_control_id" in str(error)
    else:
        raise AssertionError("invalid reference_layout_patch reached the tool body")
