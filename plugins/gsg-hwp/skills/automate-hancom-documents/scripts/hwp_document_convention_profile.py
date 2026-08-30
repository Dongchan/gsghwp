from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from hwp_document_convention_axis import (
    build_axis,
    build_format_axis,
    scan_axes,
    unobserved_axis,
)
from hwp_document_convention_contract import (
    ConventionSection,
    DocumentConventionProfile,
)
from hwp_document_convention_structure import object_layout_axes, section_axes
from hwp_document_convention_text import (
    blank_observations,
    bounded_observation_errors,
    ending_observations,
    script_slot_observations,
)
from hwp_document_style_usage import DocumentStyleUsage, paragraph_lead_prefix
from hwp_live_structure_contract import DocumentStructure


def _style_name(style_names: Mapping[int, str], style_id: int | None) -> str | None:
    return None if style_id is None else style_names.get(style_id)


def build_document_convention_profile(
    usage: DocumentStyleUsage,
    *,
    style_names: Mapping[int, str],
    page_setup: Mapping[str, JsonValue] | None = None,
    structure: DocumentStructure | None = None,
    paragraph_observation_error: str | None = None,
    context_observation_error: str | None = None,
    structure_observation_error: str | None = None,
) -> DocumentConventionProfile:
    paragraphs = tuple(
        sorted(usage.paragraph_evidence, key=lambda item: item.paragraph)
    )
    transitions: list[tuple[JsonValue, int]] = []
    for before, after in zip(paragraphs, paragraphs[1:], strict=False):
        transitions.append(
            (
                cast(
                    JsonValue,
                    {
                        "from_style_id": before.style_id,
                        "from_style_name": _style_name(
                            style_names,
                            before.style_id,
                        ),
                        "to_style_id": after.style_id,
                        "to_style_name": _style_name(
                            style_names,
                            after.style_id,
                        ),
                    },
                ),
                after.paragraph,
            )
        )
    headings = (
        (
            cast(
                JsonValue,
                {
                    "style_id": paragraph.style_id,
                    "style_name": _style_name(style_names, paragraph.style_id),
                    "heading_type": paragraph.format.heading_type,
                    "heading_level": paragraph.format.heading_level,
                },
            ),
            paragraph.paragraph,
        )
        for paragraph in paragraphs
        if paragraph.format.heading_type is not None
        or paragraph.format.heading_level is not None
    )
    markers = (
        (cast(JsonValue, marker), paragraph.paragraph)
        for paragraph in paragraphs
        if (marker := paragraph_lead_prefix(paragraph.lead_text))
    )
    numbering_usage = (
        (
            cast(
                JsonValue,
                {
                    "style_id": paragraph.style_id,
                    "heading_type": paragraph.format.heading_type,
                    "heading_level": paragraph.format.heading_level,
                },
            ),
            paragraph.paragraph,
        )
        for paragraph in paragraphs
        if paragraph.format.heading_type is not None
        or paragraph.format.heading_level is not None
    )
    paragraph_axes = (
        build_axis(
            "blank_paragraph",
            blank_observations(paragraphs),
            write_fields=(),
        ),
        build_format_axis(
            "alignment_raw",
            paragraphs,
            lambda value: value.alignment,
            write_fields=(),
        ),
        build_format_axis(
            "line_spacing_raw",
            paragraphs,
            lambda value: value.line_spacing,
            write_fields=(),
        ),
        build_format_axis(
            "left_margin_hwpunit",
            paragraphs,
            lambda value: value.left_margin_hwpunit,
            write_fields=(),
        ),
        build_format_axis(
            "right_margin_hwpunit",
            paragraphs,
            lambda value: value.right_margin_hwpunit,
            write_fields=(),
        ),
        build_format_axis(
            "indentation_hwpunit",
            paragraphs,
            lambda value: value.indentation_hwpunit,
            write_fields=(),
        ),
        build_format_axis(
            "previous_spacing_hwpunit",
            paragraphs,
            lambda value: value.previous_spacing_hwpunit,
            write_fields=(),
        ),
        build_format_axis(
            "next_spacing_hwpunit",
            paragraphs,
            lambda value: value.next_spacing_hwpunit,
            write_fields=(),
        ),
    )
    scan_complete = usage.complete and paragraph_observation_error is None
    return DocumentConventionProfile(
        scanned_paragraphs=usage.scanned_paragraphs,
        counted_paragraphs=usage.observed_paragraphs,
        complete=scan_complete,
        list_id=usage.list_id,
        observation_errors=bounded_observation_errors(
            paragraph_observation_error,
            context_observation_error,
            structure_observation_error,
        ),
        style_transitions=ConventionSection(
            axes=scan_axes(
                (
                    build_axis(
                        "adjacent_style_pair",
                        transitions,
                        write_fields=(),
                    ),
                ),
                scan_complete,
            )
        ),
        numbering_definitions=ConventionSection(
            axes=scan_axes(
                (
                    build_axis(
                        "heading_type_level",
                        headings,
                        write_fields=("blocks[].style_id",),
                    ),
                    build_axis(
                        "numbering_definition_usage",
                        numbering_usage,
                        write_fields=(),
                        scope="scanned-paragraphs",
                    ),
                    unobserved_axis(
                        "automatic_number_format",
                        "네이티브 문단 관측은 번호 정의 문자열과 연속 번호 상태를 읽지 않습니다.",
                    ),
                ),
                scan_complete,
            )
        ),
        character_runs=ConventionSection(
            axes=scan_axes(
                (
                    build_format_axis(
                        "leading_run_font",
                        paragraphs,
                        lambda value: value.face_name,
                        write_fields=("blocks[].font_name",),
                    ),
                    build_format_axis(
                        "leading_run_size_hwpunit",
                        paragraphs,
                        lambda value: value.height,
                        write_fields=(),
                    ),
                    build_format_axis(
                        "leading_run_bold",
                        paragraphs,
                        lambda value: value.bold,
                        write_fields=("blocks[].bold",),
                    ),
                    build_axis(
                        "script_font_slots",
                        script_slot_observations(paragraphs),
                        write_fields=(),
                    ),
                    unobserved_axis(
                        "intra_paragraph_run_transitions",
                        "글꼴 슬롯은 읽지만 문단 안에서 모양이 바뀌는 위치까지는 읽지 않습니다.",
                    ),
                ),
                scan_complete,
            )
        ),
        section_map=ConventionSection(
            axes=section_axes(page_setup, context_observation_error)
        ),
        object_layout=ConventionSection(
            axes=object_layout_axes(structure, structure_observation_error)
        ),
        paragraph_layout=ConventionSection(
            axes=scan_axes(
                paragraph_axes
                + (
                    build_axis(
                        "section_paragraph_spacing_policy",
                        (
                            (
                                cast(
                                    JsonValue,
                                    {
                                        "line_spacing": paragraph.format.line_spacing,
                                        "previous_spacing_hwpunit": paragraph.format.previous_spacing_hwpunit,
                                        "next_spacing_hwpunit": paragraph.format.next_spacing_hwpunit,
                                    },
                                ),
                                paragraph.paragraph,
                            )
                            for paragraph in paragraphs
                            if paragraph.format.line_spacing is not None
                            or paragraph.format.previous_spacing_hwpunit is not None
                            or paragraph.format.next_spacing_hwpunit is not None
                        ),
                        scope="scanned-paragraphs",
                    ),
                ),
                scan_complete,
            )
        ),
        prose_style=ConventionSection(
            axes=scan_axes(
                (
                    build_axis(
                        "typed_lead_marker",
                        markers,
                        write_fields=("blocks[].text",),
                    ),
                    build_axis(
                        "paragraph_ending",
                        ending_observations(paragraphs),
                        write_fields=("blocks[].text",),
                    ),
                ),
                scan_complete,
            )
        ),
    )
