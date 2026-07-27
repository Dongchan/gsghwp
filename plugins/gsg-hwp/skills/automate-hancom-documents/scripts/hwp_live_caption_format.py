from __future__ import annotations

import re
from dataclasses import dataclass

from hwp_live_contract import LayoutPlan
from hwp_live_native_action_models import NativePosition
from hwp_live_table_contract import TableBlock


_HIERARCHICAL_TABLE_CAPTION = re.compile(
    r"(?:[<(\[]\s*)?표\s+(?:\d+(?:\.\d+)+(?:-\d+)?|\d+-\d+)(?:\s*[>)\]])?"
)


@dataclass(frozen=True, slots=True)
class TableCaptionFormatSource:
    style_id: int
    position: NativePosition


def has_hierarchical_table_caption(text: str) -> bool:
    return _HIERARCHICAL_TABLE_CAPTION.search(text) is not None


def caption_text_uses_hierarchy(page_text: str, caption_text: str) -> bool:
    title = caption_text.strip()
    if not title:
        return False
    return any(
        title in page_text[match.end() : match.end() + len(title) + 64]
        for match in _HIERARCHICAL_TABLE_CAPTION.finditer(page_text)
    )


def caption_format_sources(
    plan: LayoutPlan,
    source: TableCaptionFormatSource | None,
) -> tuple[tuple[str, NativePosition], ...]:
    if source is None:
        return ()
    sources: dict[str, NativePosition] = {}
    for block in plan.blocks:
        if (
            isinstance(block, TableBlock)
            and block.caption is not None
            and block.caption_style_id == source.style_id
        ):
            sources[block.caption_style_name or ""] = source.position
    return tuple(sources.items())
