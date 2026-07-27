from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from pathlib import Path

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    NativeCharacterFormat,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
)
from hwp_live_native_format_inputs import (  # noqa: E402
    InputFailure,
    TextFormatSpec,
    parse_text_format,
)
from hwp_public_action_contract import PublicTextFormattingInput  # noqa: E402
from hwp_live_text_format_verification import verify_text_format  # noqa: E402


def _snapshot(*, text_color: int = 0, selected: bool = True) -> NativeSnapshot:
    position = NativePosition(0, 2, 4)
    return NativeSnapshot(
        document_id=17,
        full_name="C:/documents/sample.hwp",
        current_page=1,
        page_count=1,
        modified=True,
        cursor=position,
        selection=NativeSelection(selected, position, NativePosition(0, 2, 8)),
        selected_text="변경 부분",
        control_type="",
        control_instance_id="",
        cell_address="",
        style_id=0,
        character_format=NativeCharacterFormat(
            face_name="함초롬바탕",
            height_hwpunit=1_000,
            bold=False,
            text_color=text_color,
        ),
        paragraph_format=NativeParagraphFormat(
            alignment=0,
            line_spacing=160,
            left_margin_hwpunit=0,
            right_margin_hwpunit=0,
            indentation_hwpunit=0,
            previous_spacing_hwpunit=0,
            next_spacing_hwpunit=0,
        ),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("빨강", (255, 0, 0)),
        ("빨간색", (255, 0, 0)),
        ("red", (255, 0, 0)),
        ("#FF0000", (255, 0, 0)),
        ("rgb(255, 0, 0)", (255, 0, 0)),
    ),
)
def test_text_color_names_normalize_to_one_rgb(
    value: str,
    expected: tuple[int, int, int],
) -> None:
    # Given: equivalent human and canonical color spellings.
    # When: the native text-format boundary parses each spelling.
    parsed = parse_text_format({"text_color": value})

    # Then: every spelling becomes the same typed RGB value.
    assert not isinstance(parsed, InputFailure)
    assert parsed.text_color == expected


def test_public_text_color_is_canonicalized_before_execution() -> None:
    # Given: a Korean color name at the public MCP boundary.
    # When: the public formatting model parses it.
    formatting = PublicTextFormattingInput.model_validate({"text_color": "빨간색"})

    # Then: downstream code receives one canonical RGB representation.
    assert formatting.text_color == (255, 0, 0)
    assert formatting.to_parameters()["text_color"] == "#FF0000"


def test_text_color_verification_rejects_a_false_success() -> None:
    # Given: red was requested but the native readback is still black.
    requested = TextFormatSpec(
        bold=None,
        font_name=None,
        font_size_pt=None,
        text_color=(255, 0, 0),
        alignment="inherit",
        line_spacing=None,
    )

    # When/Then: operation-specific verification rejects the mismatch.
    with pytest.raises(HwpLiveError, match="글자색"):
        verify_text_format(
            requested,
            _snapshot(text_color=0),
            _snapshot(text_color=0),
        )


def test_text_color_verification_accepts_exact_native_readback() -> None:
    # Given: red was requested and HWP reports the BGR-packed RGB integer.
    requested = TextFormatSpec(
        bold=None,
        font_name=None,
        font_size_pt=None,
        text_color=(255, 0, 0),
        alignment="inherit",
        line_spacing=None,
    )

    # When/Then: the exact native readback satisfies the postcondition.
    verify_text_format(
        requested,
        _snapshot(text_color=0),
        _snapshot(text_color=255),
    )


def test_text_color_verification_rejects_a_collapsed_target_range() -> None:
    # Given: red appears at the caret but the original selected range was lost.
    requested = TextFormatSpec(
        bold=None,
        font_name=None,
        font_size_pt=None,
        text_color=(255, 0, 0),
        alignment="inherit",
        line_spacing=None,
    )

    # When/Then: a caret-only readback cannot prove the selected range changed.
    with pytest.raises(HwpLiveError, match="선택 영역"):
        verify_text_format(
            requested,
            _snapshot(text_color=0),
            _snapshot(text_color=255, selected=False),
        )
