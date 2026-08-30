from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class CheckpointTextPresence(IntEnum):
    UNKNOWN = 0
    ABSENT = 1
    PRESENT = 2


@dataclass(frozen=True, slots=True)
class CheckpointContentSignature:
    page_count: int
    control_count: int
    control_hash: int
    text_hash: int
    text_length: int
    text_empty: bool
    text_presence: CheckpointTextPresence | None
    document_hash: int | None
    document_length: int | None


def _parse_signature(raw: str) -> CheckpointContentSignature | None:
    parts = raw.split()
    if len(parts) not in (6, 7, 8, 10) or parts[0] != "SIG":
        return None
    try:
        values = [int(part) for part in parts[1:]]
    except ValueError:
        return None
    page_count, control_count, control_hash, text_hash, text_length = values[:5]
    if page_count < 1 or any(value < 0 for value in values[1:]):
        return None
    if len(values) == 5:
        text_empty = text_length == 0
    else:
        marker = values[5]
        if marker not in (0, 1):
            return None
        text_empty = marker == 1
    if len(values) in (7, 9):
        try:
            text_presence = CheckpointTextPresence(values[6])
        except ValueError:
            return None
    else:
        text_presence = None
    if len(values) == 9:
        document_hash = values[7]
        document_length = values[8]
    else:
        document_hash = None
        document_length = None
    return CheckpointContentSignature(
        page_count,
        control_count,
        control_hash,
        text_hash,
        text_length,
        text_empty,
        text_presence,
        document_hash,
        document_length,
    )


def checkpoint_signatures_match(expected: str, actual: str) -> bool:
    if not expected or not actual:
        return False
    expected_parsed = _parse_signature(expected)
    actual_parsed = _parse_signature(actual)
    if expected_parsed is None or actual_parsed is None:
        return expected == actual
    structural_match = (
        expected_parsed.page_count,
        expected_parsed.control_count,
        expected_parsed.control_hash,
    ) == (
        actual_parsed.page_count,
        actual_parsed.control_count,
        actual_parsed.control_hash,
    )
    if not structural_match:
        return False
    expected_document = (
        expected_parsed.document_hash,
        expected_parsed.document_length,
    )
    actual_document = (
        actual_parsed.document_hash,
        actual_parsed.document_length,
    )
    if expected_document != actual_document:
        return False
    expected_presence = expected_parsed.text_presence
    actual_presence = actual_parsed.text_presence
    if expected_presence is None or actual_presence is None:
        if actual_parsed.text_length == 0:
            return expected_parsed.text_length == 0
        if expected_parsed.text_length == 0:
            return True
        return (
            expected_parsed.text_hash,
            expected_parsed.text_length,
        ) == (
            actual_parsed.text_hash,
            actual_parsed.text_length,
        )
    if expected_presence != actual_presence:
        return False
    match expected_presence:
        case CheckpointTextPresence.UNKNOWN:
            return False
        case CheckpointTextPresence.ABSENT:
            return True
        case CheckpointTextPresence.PRESENT:
            if expected_parsed.text_empty or actual_parsed.text_empty:
                return False
    return (
        expected_parsed.text_hash,
        expected_parsed.text_length,
    ) == (
        actual_parsed.text_hash,
        actual_parsed.text_length,
    )


def checkpoint_signature_is_complete(raw: str) -> bool:
    parsed = _parse_signature(raw)
    if (
        parsed is None
        or parsed.document_hash is None
        or parsed.document_length is None
        or parsed.document_length <= 0
    ):
        return False
    match parsed.text_presence:
        case CheckpointTextPresence.ABSENT:
            return parsed.text_empty and parsed.text_length == 0
        case CheckpointTextPresence.PRESENT:
            return not parsed.text_empty and parsed.text_length > 0
        case CheckpointTextPresence.UNKNOWN | None:
            return False
