from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_native_action_models import NativeArrayValue  # noqa: E402
from hwp_reference_layout_contract import ReferenceStyle, TextAnchor  # noqa: E402
from hwp_reference_layout_geometry import SectionPageGeometry  # noqa: E402
from hwp_reference_layout_patch import (  # noqa: E402
    ReferenceLayoutPatchBlock,
    compile_reference_layout_patch_command,
)


def _page() -> SectionPageGeometry:
    return SectionPageGeometry(
        paper_width=10_000,
        paper_height=10_000,
        landscape=False,
        left_margin=0,
        right_margin=0,
        top_margin=0,
        bottom_margin=0,
        header=0,
        footer=0,
        gutter=0,
        gutter_type=0,
    )


def _values(
    items: Sequence[NativeArrayValue],
    name: str,
) -> tuple[int | float | str | bool, ...]:
    return tuple(
        item.value.value
        for item in sorted(items, key=lambda value: value.index)
        if item.name == name
    )


def test_text_only_patch_compiles_text_and_style_payload() -> None:
    patch = ReferenceLayoutPatchBlock(
        kind="reference_layout_patch",
        target_control_id="existing-grid",
        row_breakpoints=(0.0, 1.0),
        column_breakpoints=(0.0, 1.0),
        text_styles=(
            ReferenceStyle(
                key="changed",
                font_name="맑은 고딕",
                font_size_pt=11,
                bold=True,
                text_color=(255, 0, 0),
            ),
        ),
        text_anchors=(
            TextAnchor(
                row=0,
                column=0,
                text="변경된 문장",
                style_key="changed",
            ),
        ),
    )

    command = compile_reference_layout_patch_command(
        patch,
        _page(),
        page_number=1,
    )

    assert _values(command.array_values, "PatchColumnIndexes") == ()
    assert _values(command.array_values, "PatchRowIndexes") == ()
    assert _values(command.array_values, "TextRows") == (0,)
    assert _values(command.array_values, "TextColumns") == (0,)
    assert _values(command.array_values, "TextStyleIndexes") == (0,)
    assert _values(command.array_values, "TextValues") == ("변경된 문장",)
    assert _values(command.array_values, "StyleTextColors") == (255,)


def test_patch_merges_region_and_text_style_catalogs() -> None:
    patch = ReferenceLayoutPatchBlock(
        kind="reference_layout_patch",
        target_control_id="existing-grid",
        row_breakpoints=(0.0, 1.0),
        column_breakpoints=(0.0, 1.0),
        styles=(ReferenceStyle(key="cell", fill_color=(240, 240, 240)),),
        text_styles=(ReferenceStyle(key="text", text_color=(255, 0, 0)),),
        text_anchors=(
            TextAnchor(row=0, column=0, text="텍스트", style_key="text"),
        ),
    )

    command = compile_reference_layout_patch_command(
        patch,
        _page(),
        page_number=1,
    )

    assert _values(command.array_values, "StyleKeys") == ("cell", "text")
    assert _values(command.array_values, "TextStyleIndexes") == (1,)


def test_text_style_without_anchor_is_rejected_explicitly() -> None:
    with pytest.raises(ValidationError, match="text_styles require text_anchors"):
        _ = ReferenceLayoutPatchBlock(
            kind="reference_layout_patch",
            target_control_id="existing-grid",
            row_breakpoints=(0.0, 1.0),
            column_breakpoints=(0.0, 1.0),
            text_styles=(ReferenceStyle(key="unused", text_color=(255, 0, 0)),),
        )


def test_duplicate_text_anchor_target_is_rejected_before_mutation() -> None:
    with pytest.raises(ValidationError, match="text anchor targets must be unique"):
        _ = ReferenceLayoutPatchBlock(
            kind="reference_layout_patch",
            target_control_id="existing-grid",
            row_breakpoints=(0.0, 1.0),
            column_breakpoints=(0.0, 1.0),
            text_anchors=(
                TextAnchor(row=0, column=0, text="첫 번째"),
                TextAnchor(row=0, column=0, text="두 번째"),
            ),
        )
