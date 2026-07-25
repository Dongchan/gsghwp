from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass

from hwp_reference_layout_contract import (
    OcrWordEvidence,
    ReferenceLayoutBlock,
    TextAnchor,
)


@dataclass(frozen=True, slots=True)
class _PlacedWord:
    evidence: OcrWordEvidence
    row: int
    column: int


def _is_supplement_candidate(word: OcrWordEvidence) -> bool:
    if word.confidence < 0.8:
        return False
    text = word.text.strip()
    small = word.bottom - word.top <= 0.04
    contains_hangul = any("\uac00" <= character <= "\ud7a3" for character in text)
    contains_number = any(character.isdigit() for character in text)
    rotated = abs(word.rotation_degrees) >= 5
    return bool(text) and ((small and contains_hangul) or contains_number or rotated)


def _cell_index(value: float, breakpoints: tuple[float, ...]) -> int:
    return min(len(breakpoints) - 2, max(0, bisect_right(breakpoints, value) - 1))


def _merge_anchor(
    block: ReferenceLayoutBlock,
    row: int,
    column: int,
) -> tuple[int, int]:
    for merge in block.merges:
        if (
            merge.row <= row < merge.row + merge.row_span
            and merge.column <= column < merge.column + merge.column_span
        ):
            return merge.row, merge.column
    return row, column


def _place_word(
    block: ReferenceLayoutBlock,
    evidence: OcrWordEvidence,
) -> _PlacedWord:
    row = _cell_index(
        (evidence.top + evidence.bottom) / 2,
        block.row_breakpoints,
    )
    column = _cell_index(
        (evidence.left + evidence.right) / 2,
        block.column_breakpoints,
    )
    row, column = _merge_anchor(block, row, column)
    return _PlacedWord(evidence=evidence, row=row, column=column)


def _region_style(block: ReferenceLayoutBlock, row: int, column: int) -> str | None:
    for region in reversed(block.style_regions):
        if (
            region.top <= row < region.bottom
            and region.left <= column < region.right
        ):
            return region.style_key
    return None


def _joined_text(words: list[_PlacedWord]) -> str:
    ordered = sorted(
        words,
        key=lambda placed: (
            round(placed.evidence.top / 0.015),
            placed.evidence.left,
        ),
    )
    lines: list[list[str]] = []
    line_keys: list[int] = []
    for placed in ordered:
        line_key = round(placed.evidence.top / 0.015)
        if not line_keys or line_keys[-1] != line_key:
            line_keys.append(line_key)
            lines.append([])
        lines[-1].append(placed.evidence.text.strip())
    return "\n".join(" ".join(line) for line in lines)


def supplement_text_anchors(block: ReferenceLayoutBlock) -> tuple[TextAnchor, ...]:
    if block.ocr_mode != "auto":
        return block.text_anchors
    occupied = {(anchor.row, anchor.column) for anchor in block.text_anchors}
    occupied.update(
        (row, column)
        for gap in block.protected_gaps
        for row in range(gap.top, gap.bottom)
        for column in range(gap.left, gap.right)
    )
    grouped: defaultdict[tuple[int, int], list[_PlacedWord]] = defaultdict(list)
    for evidence in block.ocr_words:
        if not _is_supplement_candidate(evidence):
            continue
        placed = _place_word(block, evidence)
        target = (placed.row, placed.column)
        if target not in occupied:
            grouped[target].append(placed)
    supplements = tuple(
        TextAnchor(
            row=row,
            column=column,
            text=_joined_text(words),
            style_key=_region_style(block, row, column),
        )
        for (row, column), words in sorted(grouped.items())
    )
    return (*block.text_anchors, *supplements)
