from __future__ import annotations

import sys
from pathlib import Path
from typing import cast


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_anchor import require_empty_paragraph  # noqa: E402
from hwp_live_api import LiveHwpApplication  # noqa: E402


class _TextParagraph:
    ctrl_list: tuple[object, ...] = ()

    def get_pos(self) -> tuple[int, int, int]:
        return 0, 4, 3

    def get_selected_pos(self) -> tuple[bool, int, int, int, int, int, int]:
        return False, 0, 4, 3, 0, 4, 3

    def MoveParaBegin(self) -> bool:
        raise AssertionError("paragraph text must not be inspected")

    def get_text_file(self, *, format: str, option: str) -> str:
        raise AssertionError((format, option))


def test_table_anchor_allows_text_in_the_current_paragraph() -> None:
    hwp = cast(LiveHwpApplication, cast(object, _TextParagraph()))

    require_empty_paragraph(hwp, lambda: None)
