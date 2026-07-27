from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from pydantic import TypeAdapter, ValidationError


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_color_normalization import ColorInput  # noqa: E402
from hwp_live_layout_contract import ParagraphBlock  # noqa: E402
from hwp_live_table_contract import CellBorder, TableCell  # noqa: E402
from hwp_mcp_wrapper_inputs import HwpTableFormatting, HwpTextFormatting  # noqa: E402
from hwp_public_action_contract import PublicTextFormattingInput  # noqa: E402
from hwp_public_table_edit_contract import PublicTableFormattingInput  # noqa: E402
from hwp_reference_layout_contract import ReferenceStyle, VisibleEdge  # noqa: E402


_RED = (255, 0, 0)


@pytest.mark.parametrize(
    "value",
    ("빨강", "빨간색", "red", "#FF0000", (255, 0, 0)),
)
def test_every_public_color_contract_normalizes_to_rgb(
    value: str | tuple[int, int, int],
) -> None:
    assert (
        PublicTextFormattingInput.model_validate({"text_color": value}).text_color
        == _RED
    )
    assert (
        PublicTableFormattingInput.model_validate({"text_color": value}).text_color
        == _RED
    )
    assert (
        ParagraphBlock.model_validate(
            {"kind": "paragraph", "text": "본문", "text_color": value}
        ).text_color
        == _RED
    )
    assert (
        TableCell.model_validate(
            {"text_color": value, "fill_color": value}
        ).text_color
        == _RED
    )
    assert CellBorder.model_validate({"color": value}).color == _RED
    assert (
        ReferenceStyle.model_validate(
            {
                "key": "body",
                "text_color": value,
                "fill_color": value,
            }
        ).text_color
        == _RED
    )
    assert (
        VisibleEdge.model_validate(
            {
                "orientation": "horizontal",
                "line": 0,
                "start": 0,
                "end": 1,
                "color": value,
            }
        ).color
        == _RED
    )
    assert HwpTextFormatting.model_validate({"text_color": value}).text_color == _RED
    assert (
        HwpTableFormatting.model_validate(
            {"cell": "A1", "fill_color": value}
        ).fill_color
        == _RED
    )


def test_legacy_rgb_object_remains_supported() -> None:
    formatting = HwpTextFormatting.model_validate(
        {"text_color": {"r": 255, "g": 0, "b": 0}}
    )

    assert formatting.text_color == _RED


def test_operation_parameters_keep_the_existing_string_protocol() -> None:
    text = PublicTextFormattingInput(text_color=(255, 0, 0))
    table = PublicTableFormattingInput.model_validate(
        {"text_color": "빨간색", "fill_color": "red"}
    )

    assert text.to_parameters()["text_color"] == "#FF0000"
    assert table.to_parameters()["text_color"] == "#FF0000"
    assert table.to_parameters()["fill_color"] == "#FF0000"
    assert text.to_live().text_color == _RED


def test_color_input_schema_exposes_every_compatible_shape() -> None:
    schema = cast(Mapping[str, object], TypeAdapter(ColorInput).json_schema())
    raw_choices = schema["anyOf"]
    assert isinstance(raw_choices, list)
    choice_values = cast(list[object], raw_choices)
    choices = tuple(cast(Mapping[str, object], choice) for choice in choice_values)

    assert {choice.get("type") for choice in choices if "type" in choice} == {
        "string",
        "array",
    }
    assert any(choice.get("$ref") == "#/$defs/RgbObject" for choice in choices)
    definitions = cast(Mapping[str, object], schema["$defs"])
    rgb_object = cast(Mapping[str, object], definitions["RgbObject"])
    assert rgb_object["type"] == "object"


@pytest.mark.parametrize("value", ("not-a-color", (256, 0, 0), (255, 0)))
def test_invalid_color_is_rejected_at_the_public_boundary(value: object) -> None:
    with pytest.raises(ValidationError):
        _ = ParagraphBlock.model_validate(
            {"kind": "paragraph", "text": "본문", "text_color": value}
        )
