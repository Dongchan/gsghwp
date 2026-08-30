from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import cast

from pydantic import JsonValue

from hwp_document_convention_contract import (
    ConventionAxis,
    ConventionVariant,
)
from hwp_document_style_usage import (
    ObservedParagraphEvidence,
    ObservedParagraphFormat,
)

type FormatValue = str | int | bool

_SAMPLE_PARAGRAPH_LIMIT = 12


def _value_key(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sample_paragraphs(paragraphs: list[int]) -> tuple[int, ...]:
    if len(paragraphs) <= _SAMPLE_PARAGRAPH_LIMIT:
        return tuple(paragraphs)
    edge = _SAMPLE_PARAGRAPH_LIMIT // 2
    return tuple(paragraphs[:edge] + paragraphs[-edge:])


def build_axis(
    name: str,
    observations: Iterable[tuple[JsonValue, int]],
    *,
    write_fields: tuple[str, ...] = (),
    unobserved_reason: str | None = None,
    scope: str | None = None,
    complete: bool = True,
) -> ConventionAxis:
    grouped: dict[str, tuple[JsonValue, list[int]]] = {}
    for value, paragraph in observations:
        key = _value_key(value)
        if key not in grouped:
            grouped[key] = value, []
        grouped[key][1].append(paragraph)
    if not grouped:
        return ConventionAxis(
            name=name,
            state="unobserved",
            scope=scope,
            complete=False,
            sample_count=0,
            unobserved_reason=unobserved_reason
            or "이 관측 경로에서 값을 읽지 못했습니다.",
            write_fields=write_fields,
        )
    ordered = sorted(grouped.values(), key=lambda item: -len(item[1]))
    total = sum(len(paragraphs) for _, paragraphs in ordered)
    variants = tuple(
        ConventionVariant(
            value=value,
            samples=len(paragraphs),
            ratio=len(paragraphs) / total,
            sample_paragraphs=_sample_paragraphs(paragraphs),
        )
        for value, paragraphs in ordered
    )
    return ConventionAxis(
        name=name,
        state="observed" if len(variants) == 1 else "observed-but-conflicting",
        scope=scope,
        complete=complete,
        sample_count=total,
        consistency=variants[0].ratio,
        selected_value=variants[0].value,
        variants=variants,
        write_fields=write_fields,
    )


def scan_axes(
    axes: tuple[ConventionAxis, ...],
    complete: bool,
) -> tuple[ConventionAxis, ...]:
    return tuple(
        axis
        if axis.state == "unobserved"
        else axis.model_copy(update={"complete": complete})
        for axis in axes
    )


def build_format_axis(
    name: str,
    paragraphs: tuple[ObservedParagraphEvidence, ...],
    value: Callable[[ObservedParagraphFormat], FormatValue | None],
    *,
    write_fields: tuple[str, ...],
) -> ConventionAxis:
    return build_axis(
        name,
        (
            (cast(JsonValue, observed), paragraph.paragraph)
            for paragraph in paragraphs
            if (observed := value(paragraph.format)) is not None
        ),
        write_fields=write_fields,
    )


def unobserved_axis(
    name: str,
    reason: str,
    *write_fields: str,
) -> ConventionAxis:
    return build_axis(
        name,
        (),
        write_fields=tuple(write_fields),
        unobserved_reason=reason,
    )
