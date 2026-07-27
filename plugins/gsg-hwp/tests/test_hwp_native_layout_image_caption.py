from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_contract import LayoutPlan  # noqa: E402
from hwp_live_native_action_commands import (  # noqa: E402
    InsertTextCommand,
    RunCommand,
)
from hwp_live_native_action_models import NativeActionRequest  # noqa: E402
from hwp_live_native_layout import (  # noqa: E402
    NativeLayoutContext,
    build_native_layout_request,
)


IMAGE_PATH = Path("fixture.png")
PREPARED_IMAGE_PATH = Path("prepared-fixture.png")


def _build_request(
    *,
    caption: str | None,
    trailing_paragraph: bool,
) -> NativeActionRequest:
    blocks: list[dict[str, str | float]] = [
        {
            "kind": "image",
            "path": str(IMAGE_PATH),
            "width_mm": 30.0,
            "height_mm": 20.0,
        }
    ]
    if caption is not None:
        blocks[0]["caption"] = caption
    if trailing_paragraph:
        blocks.append({"kind": "paragraph", "text": "다음 문단"})
    plan = LayoutPlan.model_validate(
        {
            "target": "document_end",
            "blocks": blocks,
        }
    )
    return build_native_layout_request(
        NativeLayoutContext(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            style_ids=(),
        ),
        plan,
        {IMAGE_PATH: PREPARED_IMAGE_PATH},
    )


def test_terminal_image_caption_omits_trailing_paragraph_break() -> None:
    # Given
    request = _build_request(caption="종단 그림 캡션", trailing_paragraph=False)

    # When
    trailing_command = request.commands[-1]

    # Then
    assert trailing_command == InsertTextCommand("종단 그림 캡션")
    assert request.commands.count(RunCommand("BreakPara")) == 1


def test_nonterminal_image_caption_keeps_paragraph_flow() -> None:
    # Given
    request = _build_request(caption="중간 그림 캡션", trailing_paragraph=True)

    # When
    paragraph_breaks = request.commands.count(RunCommand("BreakPara"))

    # Then
    assert paragraph_breaks == 2
    assert request.commands[-1] == InsertTextCommand("다음 문단")


def test_terminal_uncaptioned_image_keeps_picture_paragraph_break() -> None:
    # Given
    request = _build_request(caption=None, trailing_paragraph=False)

    # When
    trailing_command = request.commands[-1]

    # Then
    assert trailing_command == RunCommand("BreakPara")
