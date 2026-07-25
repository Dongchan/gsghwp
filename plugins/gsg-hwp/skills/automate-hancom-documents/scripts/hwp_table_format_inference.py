from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Literal
from unicodedata import normalize

from hwp_errors import HwpLiveError
from hwp_live_structure_contract import StructureCell, StructureTable
from hwp_operation_contract import TableFormatCandidate


NumericValueMode = Literal["infer", "display", "base"]

_NUMBER = re.compile(
    r"(?<!\d)[+-]?(?:(?:\d{1,3}(?:[,\u00a0 ]\d{3})+)|\d+)(?:\.\d+)?"
    + r"(?![\d,.])"
)
_KOREAN_NUMBER = re.compile(r"[일이삼사오육칠팔구십백천만억조]+")
_ENGLISH_SCALES = {
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
}
_KOREAN_DIGITS = {
    "일": 1,
    "이": 2,
    "삼": 3,
    "사": 4,
    "오": 5,
    "육": 6,
    "칠": 7,
    "팔": 8,
    "구": 9,
}
_KOREAN_SMALL = {"십": 10, "백": 100, "천": 1_000}
_KOREAN_LARGE = {"만": 10_000, "억": 100_000_000, "조": 1_000_000_000_000}
_KOREAN_SCALE_CHARACTERS = frozenset((*_KOREAN_SMALL, *_KOREAN_LARGE))
_SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


@dataclass(frozen=True, slots=True)
class NumericToken:
    raw: str
    start: int
    end: int
    value: Decimal
    decimal_places: int
    grouping: str | None


@dataclass(frozen=True, slots=True)
class CellTextPatch:
    expected_text: str
    replacement: str
    occurrence: int


@dataclass(frozen=True, slots=True)
class ContextualCellEdit:
    address: str
    expected_text: str
    replacement: str
    patches: tuple[CellTextPatch, ...]
    evidence: tuple[str, ...]


class TableFormatAmbiguity(HwpLiveError):
    candidates: tuple[TableFormatCandidate, ...]

    def __init__(
        self,
        reason: str,
        candidates: tuple[TableFormatCandidate, ...] = (),
    ) -> None:
        super().__init__(reason)
        self.candidates = candidates


def _numeric_tokens(text: str) -> tuple[NumericToken, ...]:
    tokens: list[NumericToken] = []
    for match in _NUMBER.finditer(text):
        raw = match.group()
        compact = raw.replace(",", "").replace(" ", "").replace("\u00a0", "")
        try:
            value = Decimal(compact)
        except InvalidOperation:
            continue
        fraction = compact.partition(".")[2]
        grouping = (
            ","
            if "," in raw
            else "\u00a0"
            if "\u00a0" in raw
            else " "
            if " " in raw
            else None
        )
        tokens.append(
            NumericToken(
                raw=raw,
                start=match.start(),
                end=match.end(),
                value=value,
                decimal_places=len(fraction),
                grouping=grouping,
            )
        )
    return tuple(tokens)


def _literal_parts(text: str, tokens: tuple[NumericToken, ...]) -> tuple[str, ...]:
    parts: list[str] = []
    cursor = 0
    for token in tokens:
        parts.append(text[cursor : token.start])
        cursor = token.end
    parts.append(text[cursor:])
    return tuple(parts)


def _is_naked_number(text: str, tokens: tuple[NumericToken, ...]) -> bool:
    return len(tokens) == 1 and text.strip() == tokens[0].raw


def _parse_korean_number(value: str) -> int | None:
    total = 0
    section = 0
    digit = 0
    for character in value:
        if character in _KOREAN_DIGITS:
            digit = _KOREAN_DIGITS[character]
        elif character in _KOREAN_SMALL:
            section += (digit or 1) * _KOREAN_SMALL[character]
            digit = 0
        elif character in _KOREAN_LARGE:
            section += digit
            total += (section or 1) * _KOREAN_LARGE[character]
            section = 0
            digit = 0
        else:
            return None
    parsed = total + section + digit
    return parsed if parsed > 1 else None


def _scale_factors(text: str) -> frozenset[int]:
    normalized = normalize("NFKC", text).casefold()
    factors: set[int] = set()
    for match in re.finditer(r"(?:x|×)\s*([0-9][0-9,]*)", normalized):
        factors.add(int(match.group(1).replace(",", "")))
    for match in re.finditer(r"10\s*(?:\^|\*\*)\s*([0-9]+)", normalized):
        exponent = int(match.group(1))
        if 0 < exponent <= 18:
            factors.add(int("1" + "0" * exponent))
    superscript = re.search(r"10([⁰¹²³⁴⁵⁶⁷⁸⁹]+)", text)
    if superscript is not None:
        exponent = int(superscript.group(1).translate(_SUPERSCRIPT_DIGITS))
        if 0 < exponent <= 18:
            factors.add(int("1" + "0" * exponent))
    for word, factor in _ENGLISH_SCALES.items():
        if re.search(rf"\b{word}\b", normalized):
            factors.add(factor)
    for match in _KOREAN_NUMBER.finditer(normalized):
        token = match.group()
        if not any(character in _KOREAN_SCALE_CHARACTERS for character in token):
            continue
        previous = normalized[match.start() - 1] if match.start() else ""
        following = normalized[match.end()] if match.end() < len(normalized) else ""
        if ("가" <= previous <= "힣") or (
            len(token) == 1 and "가" <= following <= "힣"
        ):
            continue
        factor = _parse_korean_number(token)
        if factor is not None:
            factors.add(factor)
    return frozenset(factor for factor in factors if factor > 1)


def _owner_cells(table: StructureTable) -> tuple[StructureCell, ...]:
    return tuple(cell for cell in table.cells if cell.address == cell.owner_address)


def _target_cell(table: StructureTable, address: str) -> StructureCell:
    normalized = address.strip().upper()
    cell = next((item for item in table.cells if item.address == normalized), None)
    if cell is None:
        raise HwpLiveError(f"대상 한컴 표에 {normalized} 셀이 없습니다")
    owner = next(
        (item for item in table.cells if item.address == cell.owner_address),
        None,
    )
    if owner is None:
        raise HwpLiveError(f"{normalized} 셀의 병합 소유자를 찾지 못했습니다")
    return owner


def _context_sources(
    table: StructureTable,
    target: StructureCell,
) -> tuple[StructureCell, ...]:
    sources: dict[str, StructureCell] = {}
    for cell in _owner_cells(table):
        covers_column = cell.column <= target.column < cell.column + cell.column_span
        above = cell.row < target.row and covers_column
        adjacent = (
            cell.row == target.row
            and cell.address != target.address
            and abs(cell.column - target.column) <= 2
            and not _numeric_tokens(cell.text)
        )
        if cell.text.strip() and (above or adjacent):
            sources[cell.address] = cell
    return tuple(
        sorted(
            sources.values(),
            key=lambda cell: (
                0 if cell.row < target.row else 1,
                abs(target.row - cell.row),
                abs(target.column - cell.column),
            ),
        )
    )


def _format_evidence(
    table: StructureTable,
    target: StructureCell,
) -> tuple[str, ...]:
    sources = _context_sources(table, target)
    evidence = [f"{cell.address}:{cell.text}" for cell in sources[:8]]
    same_column = [
        cell
        for cell in _owner_cells(table)
        if cell.address != target.address
        and cell.column <= target.column < cell.column + cell.column_span
        and _numeric_tokens(cell.text)
    ]
    evidence.extend(f"{cell.address}:{cell.text}" for cell in same_column[:8])
    return tuple(dict.fromkeys(evidence))


def _inferred_scale(
    table: StructureTable,
    target: StructureCell,
    surrounding_texts: tuple[str, ...],
) -> tuple[int | None, tuple[str, ...]]:
    found: defaultdict[int, list[str]] = defaultdict(list)
    for cell in _context_sources(table, target):
        for factor in _scale_factors(cell.text):
            found[factor].append(f"{cell.address}:{cell.text}")
    for index, text in enumerate(surrounding_texts, start=1):
        for factor in _scale_factors(text):
            found[factor].append(f"surrounding-{index}:{text}")
    if not found:
        return None, ()
    if len(found) > 1:
        summary = ", ".join(
            f"{factor} ({', '.join(values[:3])})"
            for factor, values in sorted(found.items())
        )
        raise TableFormatAmbiguity(
            f"{target.address} 셀 주변에서 서로 다른 표시 배율이 발견되었습니다: {summary}"
        )
    factor, evidence = next(iter(found.items()))
    return factor, tuple(evidence)


def _pattern_key(text: str) -> tuple[object, ...] | None:
    tokens = _numeric_tokens(text)
    if not tokens:
        return None
    return (
        _literal_parts(text, tokens),
        tuple(token.decimal_places for token in tokens),
        tuple(token.grouping for token in tokens),
    )


def _template_text(table: StructureTable, target: StructureCell) -> str:
    if target.text:
        return target.text
    grouped: defaultdict[tuple[object, ...], list[StructureCell]] = defaultdict(list)
    for cell in _owner_cells(table):
        if (
            cell.address != target.address
            and cell.column <= target.column < cell.column + cell.column_span
            and (key := _pattern_key(cell.text)) is not None
        ):
            grouped[key].append(cell)
    if not grouped:
        return ""
    ranked = sorted(
        grouped.values(),
        key=lambda cells: (-len(cells), min(abs(target.row - cell.row) for cell in cells)),
    )
    if len(ranked[0]) < 2 or (
        len(ranked) > 1 and len(ranked[0]) == len(ranked[1])
    ):
        candidates = ", ".join(
            f"{cell.address}={cell.text!r}" for cells in ranked[:3] for cell in cells[:2]
        )
        raise TableFormatAmbiguity(
            f"{target.address} 빈 셀에 적용할 같은 열의 표시 형식이 하나로 정해지지 않습니다"
            + (f": {candidates}" if candidates else "")
        )
    return min(ranked[0], key=lambda cell: abs(target.row - cell.row)).text


def _format_decimal(
    value: Decimal,
    pattern: NumericToken,
    *,
    divisor: int,
) -> str:
    displayed = value / Decimal(divisor)
    quantum = Decimal(1).scaleb(-pattern.decimal_places)
    quantized = displayed.quantize(quantum)
    if quantized != displayed:
        raise TableFormatAmbiguity(
            "기존 소수 자릿수로 값을 표시하면 숫자가 반올림되어 자동 변경할 수 없습니다"
        )
    absolute = abs(quantized)
    formatted = f"{absolute:.{pattern.decimal_places}f}"
    if pattern.grouping is not None:
        integer, dot, fraction = formatted.partition(".")
        grouped = f"{int(integer):,}"
        if pattern.grouping != ",":
            grouped = grouped.replace(",", pattern.grouping)
        formatted = grouped + (dot + fraction if dot else "")
    else:
        old_integer = pattern.raw.lstrip("+-").partition(".")[0]
        if len(old_integer) > 1 and old_integer.startswith("0"):
            integer, dot, fraction = formatted.partition(".")
            formatted = integer.zfill(len(old_integer)) + (dot + fraction if dot else "")
    if quantized < 0:
        return f"-{formatted}"
    if pattern.raw.startswith("+"):
        return f"+{formatted}"
    return formatted


def _relative_magnitude(left: Decimal, right: Decimal) -> Decimal | None:
    left = abs(left)
    right = abs(right)
    if left == 0 or right == 0:
        return None
    return max(left / right, right / left)


def _scaled_token_indices(
    template_tokens: tuple[NumericToken, ...],
    requested_tokens: tuple[NumericToken, ...],
    scale: int,
) -> frozenset[int]:
    if len(template_tokens) != len(requested_tokens):
        return frozenset()
    scaled: set[int] = set()
    for index, (template, requested) in enumerate(
        zip(template_tokens, requested_tokens, strict=True)
    ):
        if requested.value == template.value:
            continue
        display_distance = _relative_magnitude(requested.value, template.value)
        base_distance = _relative_magnitude(
            requested.value / Decimal(scale),
            template.value,
        )
        if (
            display_distance is not None
            and base_distance is not None
            and base_distance <= display_distance
        ):
            scaled.add(index)
    return frozenset(scaled)


def _rebuild_text(
    template: str,
    requested: str,
    *,
    divisor: int,
    scaled_token_indices: frozenset[int] | None = None,
) -> str:
    if requested == "":
        return ""
    template_tokens = _numeric_tokens(template)
    requested_tokens = _numeric_tokens(requested)
    if not template_tokens:
        if requested_tokens and template:
            raise TableFormatAmbiguity(
                "기존 셀은 숫자 표시 형식이 아닌데 새 값은 숫자여서 자동 형식 보존이 모호합니다"
            )
        return requested
    if not requested_tokens:
        raise TableFormatAmbiguity(
            "기존 숫자 셀을 숫자가 없는 값으로 바꾸면 단위·괄호 관계를 보존할 수 없습니다"
        )
    template_literals = _literal_parts(template, template_tokens)
    requested_literals = _literal_parts(requested, requested_tokens)
    if _is_naked_number(requested, requested_tokens) and len(template_tokens) == 1:
        values = requested_tokens
    elif (
        len(requested_tokens) == len(template_tokens)
        and requested_literals == template_literals
    ):
        values = requested_tokens
    else:
        raise TableFormatAmbiguity(
            "새 값의 숫자 개수·단위·괄호·줄바꿈 구조가 기존 셀과 달라 자동 변경하지 않았습니다"
        )
    rebuilt = [template_literals[0]]
    for index, (pattern, value) in enumerate(
        zip(template_tokens, values, strict=True)
    ):
        token_divisor = (
            divisor
            if scaled_token_indices is None or index in scaled_token_indices
            else 1
        )
        rebuilt.append(
            _format_decimal(value.value, pattern, divisor=token_divisor)
        )
        rebuilt.append(template_literals[index + 1])
    return "".join(rebuilt)


def _non_overlapping_occurrences(text: str, expected: str) -> tuple[int, ...]:
    starts: list[int] = []
    cursor = 0
    while (found := text.find(expected, cursor)) >= 0:
        starts.append(found)
        cursor = found + len(expected)
    return tuple(starts)


def _patches(expected: str, replacement: str) -> tuple[CellTextPatch, ...]:
    if expected == replacement or not expected:
        return ()
    before = _numeric_tokens(expected)
    after = _numeric_tokens(replacement)
    if (
        before
        and len(before) == len(after)
        and _literal_parts(expected, before) == _literal_parts(replacement, after)
    ):
        current = expected
        planned: list[CellTextPatch] = []
        for old, new in reversed(tuple(zip(before, after, strict=True))):
            if old.raw == new.raw:
                continue
            starts = _non_overlapping_occurrences(current, old.raw)
            if old.start not in starts:
                raise TableFormatAmbiguity(
                    "숫자 토큰이 서로 겹쳐 변경 범위를 안전하게 특정할 수 없습니다"
                )
            occurrence = starts.index(old.start) + 1
            planned.append(CellTextPatch(old.raw, new.raw, occurrence))
            current = current[: old.start] + new.raw + current[old.end :]
        if current == replacement:
            return tuple(planned)
    return (CellTextPatch(expected, replacement, 1),)


def _magnitude_evidence(table: StructureTable, target: StructureCell) -> str | None:
    magnitudes = [
        abs(float(token.value))
        for cell in _owner_cells(table)
        if cell.address != target.address
        and cell.column <= target.column < cell.column + cell.column_span
        for token in _numeric_tokens(cell.text)
        if token.value
    ]
    if len(magnitudes) < 2:
        return None
    return f"same-column-median:{median(magnitudes):.12g}"


def infer_table_cell_edits(
    table: StructureTable,
    replacements: tuple[tuple[str, str], ...],
    *,
    numeric_value_mode: NumericValueMode,
    surrounding_texts: tuple[str, ...] = (),
) -> tuple[ContextualCellEdit, ...]:
    edits: list[ContextualCellEdit] = []
    for address, requested in replacements:
        target = _target_cell(table, address)
        if target.address != address:
            raise HwpLiveError(
                f"{address} 셀은 병합된 {target.address} 셀로만 수정할 수 있습니다"
            )
        if requested == target.text:
            continue
        template = _template_text(table, target)
        requested_tokens = _numeric_tokens(requested)
        scale, scale_evidence = _inferred_scale(
            table,
            target,
            surrounding_texts,
        )
        evidence = list(_format_evidence(table, target))
        if magnitude := _magnitude_evidence(table, target):
            evidence.append(magnitude)
        evidence.extend(scale_evidence)
        evidence_tuple = tuple(dict.fromkeys(evidence))
        template_tokens = _numeric_tokens(template)
        scaled_token_indices = (
            frozenset()
            if scale is None
            else _scaled_token_indices(template_tokens, requested_tokens, scale)
        )
        if (
            numeric_value_mode == "base"
            and requested_tokens
            and (scale is None or not scaled_token_indices)
        ):
            raise TableFormatAmbiguity(
                f"{address} 셀 주변에서 기준값을 표시값으로 안전하게 바꿀 "
                "배율·숫자 토큰을 하나로 찾지 못했습니다"
            )
        if (
            numeric_value_mode == "infer"
            and scale is not None
            and scaled_token_indices
        ):
            display = _rebuild_text(template, requested, divisor=1)
            base = _rebuild_text(
                template,
                requested,
                divisor=scale,
                scaled_token_indices=scaled_token_indices,
            )
            candidates = (
                TableFormatCandidate(
                    address=address,
                    numeric_value_mode="display",
                    replacement=display,
                    inferred_scale=scale,
                    evidence=evidence_tuple,
                ),
                TableFormatCandidate(
                    address=address,
                    numeric_value_mode="base",
                    replacement=base,
                    inferred_scale=scale,
                    evidence=evidence_tuple,
                ),
            )
            raise TableFormatAmbiguity(
                f"{address} 셀의 입력값이 이미 표시 배율을 적용한 값인지 기준 단위 값인지 "
                + "확인해야 합니다",
                candidates,
            )
        divisor = scale if numeric_value_mode == "base" and scale is not None else 1
        replacement = (
            _rebuild_text(
                template,
                requested,
                divisor=divisor,
                scaled_token_indices=(
                    scaled_token_indices
                    if numeric_value_mode == "base" and scale is not None
                    else None
                ),
            )
            if template
            else requested
        )
        if replacement == target.text:
            continue
        edits.append(
            ContextualCellEdit(
                address=address,
                expected_text=target.text,
                replacement=replacement,
                patches=_patches(target.text, replacement),
                evidence=evidence_tuple,
            )
        )
    return tuple(edits)
