from __future__ import annotations

import re
from typing import cast

from pydantic import JsonValue

from hwp_document_style_usage import ObservedParagraphEvidence

_NUMBER = re.compile(r"[+-]?(?:\d[\d,]*(?:\.\d+)?)")


def bounded_observation_errors(
    *errors: str | None,
) -> tuple[str, ...]:
    return tuple(error[:300] for error in errors if error)


def _paragraph_ending(tail_text: str) -> str:
    stripped = tail_text.rstrip()
    return stripped.rsplit(maxsplit=1)[-1][-32:] if stripped else ""


def ending_observations(
    paragraphs: tuple[ObservedParagraphEvidence, ...],
) -> tuple[tuple[JsonValue, int], ...]:
    return tuple(
        (cast(JsonValue, ending), paragraph.paragraph)
        for paragraph in paragraphs
        if paragraph.text_complete
        and (ending := _paragraph_ending(paragraph.tail_text))
    )


def blank_observations(
    paragraphs: tuple[ObservedParagraphEvidence, ...],
) -> tuple[tuple[JsonValue, int], ...]:
    return tuple(
        (cast(JsonValue, paragraph.text_length == 0), paragraph.paragraph)
        for paragraph in paragraphs
        if paragraph.text_complete and paragraph.text_length is not None
    )


def numeric_display_pattern(text: str) -> JsonValue | None:
    stripped = text.strip()
    match = _NUMBER.search(stripped)
    if match is None:
        return None
    token = "".join(
        "0" if character.isdecimal() else character for character in match[0]
    )
    return cast(
        JsonValue,
        {
            "prefix": stripped[: match.start()].strip()[-24:],
            "number": token,
            "suffix": stripped[match.end() :].strip()[:24],
        },
    )


def script_slot_observations(
    paragraphs: tuple[ObservedParagraphEvidence, ...],
) -> tuple[tuple[JsonValue, int], ...]:
    return tuple(
        (
            cast(
                JsonValue,
                {
                    "hangul": paragraph.format.face_name,
                    "latin_and_digits": paragraph.format.face_name_latin,
                    "hanja": paragraph.format.face_name_hanja,
                    "japanese": paragraph.format.face_name_japanese,
                    "other": paragraph.format.face_name_other,
                    "symbol": paragraph.format.face_name_symbol,
                    "user": paragraph.format.face_name_user,
                },
            ),
            paragraph.paragraph,
        )
        for paragraph in paragraphs
        if any(
            (
                paragraph.format.face_name,
                paragraph.format.face_name_latin,
                paragraph.format.face_name_hanja,
                paragraph.format.face_name_japanese,
                paragraph.format.face_name_other,
                paragraph.format.face_name_symbol,
                paragraph.format.face_name_user,
            )
        )
    )
