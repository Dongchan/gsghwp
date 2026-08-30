from __future__ import annotations

import re
from dataclasses import dataclass

from hwp_document_style_usage import DocumentStyleUsage
from hwp_live_contract import LayoutPlan
from hwp_live_native_action_models import NativePosition
from hwp_live_table_contract import TableBlock


_HIERARCHICAL_NUMBER = r"\d+(?:[^\w\s]+\d+)+"
# Numeric-only wrappers are measurements or prose facts (dates, ranges, ratios),
# not caption evidence. [^\W\d_] means any Unicode letter; no language or label
# vocabulary is embedded here.
_LABELLED_NUMBER = rf"(?=[^>)\]\r\n]*[^\W\d_])[^>)\]\r\n]*{_HIERARCHICAL_NUMBER}"
_HIERARCHICAL_CAPTION_MARKER = re.compile(
    "|".join(
        (
            rf"<{_LABELLED_NUMBER}\s*>",
            rf"\({_LABELLED_NUMBER}\s*\)",
            rf"\[{_LABELLED_NUMBER}\s*\]",
        )
    )
)
_LEADING_LABELLED_HIERARCHY = re.compile(
    rf"^\s*[<(\[]?\s*(?=[^\d\r\n]*[^\W\d_])[^\d\r\n]*{_HIERARCHICAL_NUMBER}"
)


@dataclass(frozen=True, slots=True)
class TableCaptionFormatSource:
    style_id: int
    position: NativePosition


def has_hierarchical_table_caption(text: str) -> bool:
    return _HIERARCHICAL_CAPTION_MARKER.search(text) is not None


def observed_caption_prefixes(usage: DocumentStyleUsage) -> tuple[tuple[int, str], ...]:
    """Repeated same-style prefixes whose samples carry labelled hierarchy."""
    observed: list[tuple[int, str]] = []
    for run in usage.observed_styles:
        prefix = run.shared_lead_prefix
        matching_samples = sum(
            _LEADING_LABELLED_HIERARCHY.search(sample) is not None
            for sample in run.lead_samples
        )
        if prefix and run.paragraphs >= 2 and matching_samples >= 2:
            observed.append((run.style_id, prefix))
    return tuple(observed)


def text_uses_observed_caption_prefix(
    text: str,
    usage: DocumentStyleUsage,
    *,
    style_id: int | None = None,
) -> bool:
    stripped = text.strip()
    return _LEADING_LABELLED_HIERARCHY.search(stripped) is not None and any(
        (style_id is None or observed_style == style_id) and stripped.startswith(prefix)
        for observed_style, prefix in observed_caption_prefixes(usage)
    )


def page_uses_observed_caption_prefix(
    page_text: str,
    usage: DocumentStyleUsage,
) -> bool:
    return any(
        prefix in page_text for _style_id, prefix in observed_caption_prefixes(usage)
    )


def caption_text_uses_hierarchy(page_text: str, caption_text: str) -> bool:
    title = caption_text.strip()
    if not title:
        return False
    for match in _HIERARCHICAL_CAPTION_MARKER.finditer(page_text):
        suffix = page_text[match.end() :].lstrip(" \t\u00a0")
        if suffix.startswith(title):
            return True
    return False


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
