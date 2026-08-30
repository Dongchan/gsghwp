"""[leading shape -> style id] learned from the open document's own body.

Style *names* are not a reliable index. A real report ships ``동그라미`` for the
``○`` bullet and ``하이픈`` for ``-``; matching a style whose name looks like the
glyph can never find those, so ``○`` paragraphs fell through to 바탕글 and the
glyph got typed as literal text, which loses the hanging indent on wrap.

The document answers the question directly: scan its paragraphs, fold them by
leading shape, and take the style id it actually applies to that shape. Names
are never consulted here.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final
from unicodedata import category, name as unicode_name, normalize


BODY_SHAPE: Final = "body"


def _spaced_triplet_marker(text: str) -> tuple[str, str]:
    """A generic one-letter / number / one-letter lead such as ``제 1 장``."""
    spans: list[tuple[int, int]] = []
    index = 0
    for _part in range(3):
        while index < len(text) and text[index].isspace():
            index += 1
        start = index
        while index < len(text) and not text[index].isspace():
            index += 1
        if start == index:
            return "", ""
        spans.append((start, index))
    first = text[slice(*spans[0])]
    middle = text[slice(*spans[1])]
    last = text[slice(*spans[2])]
    if not (
        len(first) == 1
        and category(first)[0] == "L"
        and middle.isdecimal()
        and len(last) == 1
        and category(last)[0] == "L"
    ):
        return "", ""
    end = spans[-1][1]
    whitespace_end = end
    while whitespace_end < len(text) and text[whitespace_end].isspace():
        whitespace_end += 1
    return text[:end], text[end:whitespace_end]


def _is_structural_token(token: str) -> bool:
    """Whether Unicode classes alone give a token marker-like structure."""
    classes = tuple(category(char) for char in token)
    if any(value in {"Pi", "Pf"} for value in classes):
        return False
    kinds = tuple(
        "L"
        if value[0] == "L"
        else "N"
        if value[0] == "N" or value == "Co"
        else "P"
        if value[0] in {"P", "S"}
        else value
        for value in classes
    )
    if "P" in kinds and all(value in {"N", "P", "M"} for value in kinds):
        return "N" in kinds
    if kinds.count("L") == 1 and all(value in {"L", "P", "M"} for value in kinds):
        return "P" in kinds
    collapsed = tuple(
        value
        for index, value in enumerate(kinds)
        if index == 0 or value != kinds[index - 1]
    )
    return collapsed == ("L", "N", "L")


def _marker_token(value: str) -> tuple[str, str]:
    """Return the raw leading marker and its observed following whitespace.

    This has no marker vocabulary. Unicode character classes and the first
    whitespace boundary describe what the document wrote. Opening punctuation
    must close inside that first token, so ``「도시`` is not mistaken for a
    marker.
    """
    text = value.lstrip()
    if not text:
        return "", ""
    spaced_marker, spaced_whitespace = _spaced_triplet_marker(text)
    if spaced_marker:
        return spaced_marker, spaced_whitespace
    first_category = category(text[0])
    if first_category in {"Pi", "Pf"}:
        return "", ""
    if first_category == "Ps":
        token_end = next(
            (index for index, char in enumerate(text, start=1) if char.isspace()),
            len(text) + 1,
        )
        marker_end = next(
            (
                index
                for index, char in enumerate(text[1 : token_end - 1], start=2)
                if category(char) == "Pe"
            ),
            0,
        )
        if marker_end == 0:
            return "", ""
        marker = text[:marker_end]
        inner = marker[1:-1]
        if not (
            inner.isdecimal()
            or len(inner) == 1
            and (category(inner)[0] in {"L", "N"} or category(inner) == "Co")
        ):
            return "", ""
    elif (
        first_category[0] == "S"
        or first_category in {"Co", "No"}
        or first_category[0] == "P"
        and first_category not in {"Pi", "Pf"}
    ):
        marker = text[0]
    else:
        marker = text.split(maxsplit=1)[0]
        if not _is_structural_token(marker):
            return "", ""
    remainder = text[len(marker) :]
    whitespace = remainder[: len(remainder) - len(remainder.lstrip())]
    return marker, whitespace


def _shape_signature(marker: str) -> str:
    """A vocabulary-free structural signature for one observed marker."""
    parts: list[str] = []
    previous = ""
    collapsible = {"N", "M", "Lo", "Lu", "Ll", "Lt", "Lm"}
    for char in normalize("NFKC", marker):
        char_category = category(char)
        if char.isspace():
            continue
        if char_category[0] == "L":
            part = char_category
        elif char_category[0] == "N":
            part = "N"
        elif char_category[0] == "M":
            part = "M"
        elif char_category == "Co":
            part = "Co"
        else:
            part = char
        if part != previous or part not in collapsible:
            parts.append(part)
        previous = part
    return "".join(parts)


def _shape_label(marker: str) -> str:
    """Stable legacy labels where structure alone names them, else a signature.

    These labels preserve the public observation vocabulary for shapes it
    already reported. They are not an allow-list: every other token still gets
    a ``marker:...`` signature and participates in observation.
    """
    normalized = normalize("NFKC", marker)
    compact = "".join(char for char in normalized if not char.isspace())
    signature = _shape_signature(marker)
    if (
        len(compact) >= 3
        and compact[0] == "제"
        and compact[-1] == "장"
        and compact[1:-1].isdecimal()
    ):
        return "chapter"
    if signature == "N.N.N":
        return "decimal3"
    if signature == "N.N":
        return "decimal2"
    if signature == "N)":
        return "number_paren"
    if signature == "(N)":
        return "number_bracket"
    if normalized and 0xAC00 <= ord(normalized[0]) <= 0xD7A3:
        if signature == "Lo.":
            return "korean_dot"
        if signature == "Lo)":
            return "korean_paren"
    if len(marker) == 1:
        marker_category = category(marker)
        marker_name = unicode_name(marker, "")
        if marker_category == "Pd":
            return "dash_bullet"
        if "REFERENCE MARK" in marker_name:
            return "reference_mark"
        if "WHITE CIRCLE" in marker_name or "WHITE BULLET" in marker_name:
            return "circle_bullet"
        if "BLACK" in marker_name and (
            "CIRCLE" in marker_name
            or "BULLET" in marker_name
            or "SMALL SQUARE" in marker_name
        ):
            return "filled_circle_bullet"
        if "MIDDLE DOT" in marker_name or "BULLET OPERATOR" in marker_name:
            return "middle_dot"
        if "SQUARE" in marker_name:
            return "square_bullet"
    return f"marker:{signature}"


def paragraph_shape(value: str) -> str | None:
    """Structural signature of the raw leading token, or ``None``."""
    marker, _whitespace = _marker_token(value)
    return None if not marker else _shape_label(marker)


def paragraph_lead_marker(value: str) -> str:
    """The marker this paragraph actually opens with, verbatim.

    Classification may use NFKC, but this answer never does: a document that
    writes ``㉮`` gets ``㉮`` back rather than its compatibility form ``(가)``.
    """
    marker, _whitespace = _marker_token(value)
    return marker


def paragraph_lead_prefix(value: str) -> str:
    """Raw marker plus the exact following whitespace observed in the document."""
    marker, whitespace = _marker_token(value)
    return marker + whitespace if marker else ""


def paragraph_marker_is_unambiguous(value: str) -> bool:
    """Whether a caller-written marker is structural without naming its glyph."""
    marker = paragraph_lead_marker(value)
    if not marker:
        return False
    if len(marker) == 1:
        marker_category = category(marker)
        return marker_category[0] in {"P", "S"} or marker_category in {"Co", "No"}
    return category(marker[0]) == "Ps" and category(marker[-1]) == "Pe"


@dataclass(frozen=True, slots=True)
class ObservedParagraphFormat:
    """One paragraph's own appearance, exactly as the bridge read it.

    Every field is ``None`` when nobody could read it. Absent is not zero: an
    indentation of 0 and an indentation we never saw are different answers, and
    replaying the second one would flatten a document that hangs its bullets.
    """

    face_name: str | None = None
    face_name_latin: str | None = None
    face_name_hanja: str | None = None
    face_name_japanese: str | None = None
    face_name_other: str | None = None
    face_name_symbol: str | None = None
    face_name_user: str | None = None
    height: int | None = None
    bold: bool | None = None
    text_color: int | None = None
    alignment: int | None = None
    line_spacing: int | None = None
    left_margin_hwpunit: int | None = None
    right_margin_hwpunit: int | None = None
    indentation_hwpunit: int | None = None
    previous_spacing_hwpunit: int | None = None
    next_spacing_hwpunit: int | None = None
    # Whether HWP itself numbers or bullets this paragraph. Best effort and
    # still unverified against a live HWP build, but it is acted on, not merely
    # reported, in two places:
    #   - hwp_document_style_profile._replicated_updates copies it onto the
    #     inserted paragraph only when it reads 0 ("HWP draws nothing here").
    #     Switching numbering *on* is deliberately never done.
    #   - hwp_document_style_profile._observed_body_style refuses a body style
    #     whose marker is automatic (ObservedStyleRun.marker_is_automatic),
    #     which is derived from this field.
    heading_type: int | None = None
    heading_level: int | None = None

    @property
    def empty(self) -> bool:
        return all(
            value is None
            for value in (
                self.face_name,
                self.height,
                self.bold,
                self.text_color,
                self.alignment,
                self.line_spacing,
                self.left_margin_hwpunit,
                self.right_margin_hwpunit,
                self.indentation_hwpunit,
                self.previous_spacing_hwpunit,
                self.next_spacing_hwpunit,
                self.heading_type,
                self.heading_level,
            )
        )


@dataclass(frozen=True, slots=True)
class ObservedStyleRun:
    """One style as the open document actually uses it.

    Nothing here is matched against a vocabulary we wrote down. The counts are
    counted and ``lead_samples`` are the document's own leading characters,
    verbatim, so a document that numbers its headings ``제1편`` or bullets them
    with ``◈`` describes itself exactly as well as one that uses the shapes
    ``paragraph_shape`` happens to know. ``shared_lead_prefix`` is the longest
    prefix shared by the largest group of the style's observed paragraphs, not
    by all of them (``_dominant_lead_prefix``) -- for a marker style that is the
    marker, and for prose it is empty, which is itself the honest answer. It is
    a majority because unanimity is not stable: one appended ``○`` in a run of
    ``◦`` was enough to collapse the answer to "", so the same document read
    differently before and after a reload.
    """

    style_id: int
    paragraphs: int
    first_paragraph: int
    shared_lead_prefix: str
    lead_samples: tuple[str, ...]
    # The marker the majority of this style's paragraphs open with, verbatim,
    # and the axis-wise representative appearance of the real paragraphs.
    # Character axes only count paragraphs with text; paragraph axes also count
    # blanks because a blank paragraph still owns margins and indentation.
    lead_marker: str = ""
    # A raw marker plus separator repeated by a strict majority of this style's
    # non-automatic text paragraphs. This stronger signal is safe to generate;
    # ``lead_marker`` alone is only an observation and may be a serial token.
    fixed_lead_prefix: str = ""
    format: ObservedParagraphFormat | None = None
    # First real paragraph among those supporting the most winning axes. The
    # format above may combine independently voted axes, so this is a
    # traceability pointer rather than a claim that one paragraph owns them all.
    representative_paragraph: int = 0

    @property
    def marker_is_automatic(self) -> bool | None:
        """Does HWP draw this style's marker itself, or is it typed as text?

        ``ParaShape/HeadingType`` per the official catalog: 0 none, 1 outline,
        2 number, 3 bullet. ``None`` means the property could not be read and
        the caller must not guess.

        What this answers is *whether* HWP numbers the paragraph, never *which*
        number it prints -- that lives in ``ParaShape/Numbering``, a ``PIT_SET``
        the bridge cannot descend into. So this is evidence about the document,
        not an instruction to type or delete a marker: a live run showed four
        different styles of this document (``1)``, ``가)``, ``표타이틀``,
        ``그림타이틀``) all reporting the same ``HeadingType=2, Level=0``
        while their numbering definitions printed ``1)``, ``가)``,
        ``<표 5.5.3-^1>`` and ``(그림 5.5.3-^1)``.
        """
        if self.format is None or self.format.heading_type is None:
            return None
        return self.format.heading_type >= 1


@dataclass(frozen=True, slots=True)
class ObservedParagraphEvidence:
    paragraph: int
    lead_text: str
    style_id: int | None
    format: ObservedParagraphFormat
    tail_text: str = ""
    text_length: int | None = None
    text_complete: bool = False


@dataclass(frozen=True, slots=True)
class DocumentStyleUsage:
    """What style id the document pairs with each leading shape."""

    shape_styles: Mapping[str, int]
    observed_paragraphs: int = 0
    complete: bool = True
    # How many paragraphs the scan actually visited, blank ones included.
    # ``observed_paragraphs`` is the subset that carried evidence, so the two
    # together say how much of the document the counts below describe.
    scanned_paragraphs: int = 0
    # Every style the scan saw, in the order the document first uses it. The
    # shape table above answers "which style do I apply to a ``○`` line"; this
    # answers "what does this document do", which is a different question and
    # the only one a reader can check.
    observed_styles: tuple[ObservedStyleRun, ...] = ()
    # Which paragraph list the scan walked. Body text is 0; a representative
    # paragraph index means nothing without it.
    list_id: int = 0
    # Compact source rows retained for the evidence-bearing convention profile.
    # They are not serialized directly and carry no invented defaults.
    paragraph_evidence: tuple[ObservedParagraphEvidence, ...] = ()

    def style_for_shape(self, shape: str | None) -> int | None:
        return self.shape_styles.get(BODY_SHAPE if shape is None else shape)

    @property
    def body_style_id(self) -> int | None:
        return self.shape_styles.get(BODY_SHAPE)

    def _run(self, style_id: int | None) -> ObservedStyleRun | None:
        if style_id is None:
            return None
        return next(
            (run for run in self.observed_styles if run.style_id == style_id),
            None,
        )

    def format_for_style(self, style_id: int | None) -> ObservedParagraphFormat | None:
        """How the document's own paragraphs of this style actually look.

        Keyed by style rather than by leading shape on purpose: a caller that
        resolved to a style -- however it resolved, observed table or style
        name -- is about to write a paragraph of that style, and the shape it
        should wear is the one the document's paragraphs of that style wear.
        """
        run = self._run(style_id)
        return None if run is None else run.format

    def marker_for_style(self, style_id: int | None) -> str:
        """The marker the document's own paragraphs of this style open with."""
        run = self._run(style_id)
        return "" if run is None else run.lead_marker

    def marker_is_automatic(self, style_id: int | None) -> bool | None:
        """Whether HWP draws this style's marker itself. ``None`` = unknown."""
        run = self._run(style_id)
        return None if run is None else run.marker_is_automatic

    def preferred_typed_marker(self) -> tuple[int, str] | None:
        """Most-used stable marker prefix typed by this document itself."""
        candidates = tuple(
            run
            for run in self.observed_styles
            if run.fixed_lead_prefix and run.marker_is_automatic is False
        )
        if not candidates:
            return None
        selected = max(candidates, key=lambda run: run.paragraphs)
        return selected.style_id, selected.fixed_lead_prefix


EMPTY_STYLE_USAGE: Final = DocumentStyleUsage(shape_styles={})


def _shared_prefix(left: str, right: str) -> str:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return left[:index]


def _majority[T: Hashable](values: Sequence[T]) -> T | None:
    """The value most of these paragraphs agree on, first observed on a tie.

    Folding by "what every paragraph agrees on" made a single outlier erase the
    convention: one paragraph we ourselves appended with ``○`` was enough to
    turn a style whose whole run opens with ``◦`` into "no shared prefix", and
    the same table then read differently before and after a reload. What most
    of the document does is stable under one stray paragraph; what all of it
    does is not.
    """
    if not values:
        return None
    counts: dict[T, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    # dict keeps insertion order and max() returns the first maximal entry, so
    # equal counts resolve to whichever value the document used first.
    return max(counts.items(), key=lambda item: item[1])[0]


def _present_majority[T: Hashable](values: Iterable[T | None]) -> T | None:
    """Most common reported value; unreadable ``None`` carries no vote."""
    return _majority(tuple(value for value in values if value is not None))


def _fixed_lead_prefix(prefixes: Sequence[str], eligible: int) -> str:
    """A repeated literal prefix, only when it dominates eligible paragraphs."""
    selected = _majority(prefixes)
    if selected is None:
        return ""
    count = sum(prefix == selected for prefix in prefixes)
    return selected if count >= 2 and count * 2 > eligible else ""


def _dominant_lead_prefix(leads: Sequence[str]) -> str:
    """Longest prefix shared by the largest group of these paragraphs.

    Grouping by first character before folding is what keeps one stray
    paragraph from collapsing the answer to "" -- see ``_majority``.
    """
    if len(leads) < 2:
        # One paragraph shares its whole text with itself, which would print a
        # sentence where a marker belongs. A prefix is evidence of a convention
        # only once two paragraphs agree on it; below that the honest answer is
        # nothing, and the sample still shows the text.
        return ""
    groups: dict[str, list[str]] = {}
    for lead in leads:
        groups.setdefault(lead[0], []).append(lead)
    largest = max(groups.values(), key=len)
    if len(largest) < 2:
        return ""
    prefix = largest[0]
    for lead in largest[1:]:
        prefix = _shared_prefix(prefix, lead)
    return prefix


type ObservedParagraphRecord = (
    tuple[int, str, int | None]
    | tuple[int, str, int | None, ObservedParagraphFormat | None]
)
type _ObservedFormatRecord = tuple[int, bool, ObservedParagraphFormat]


def build_observed_style_runs(
    paragraphs: Iterable[ObservedParagraphRecord],
    *,
    sample_limit: int = 3,
) -> tuple[ObservedStyleRun, ...]:
    """Fold observed paragraphs by style id, keeping one representative each.

    Records are ``(index, leading text, style id)`` and optionally the
    paragraph's own observed appearance. Blank paragraphs do not count as text
    usage or marker evidence, but their paragraph shape does vote: an empty HWP
    paragraph still owns margins and indentation. Unlike that function nothing
    is dropped here -- the body style is reported as loudly as a heading style,
    because a caller that cannot see how often the document uses plain body
    text cannot tell a heading style from a one-off.
    """
    counts: dict[int, int] = {}
    firsts: dict[int, int] = {}
    leads: dict[int, list[str]] = {}
    markers: dict[int, list[str]] = {}
    marker_prefixes: dict[int, list[str]] = {}
    marker_eligible: dict[int, int] = {}
    formats: dict[int, list[_ObservedFormatRecord]] = {}
    samples: dict[int, list[str]] = {}
    for record in paragraphs:
        index, text, style_id = record[0], record[1], record[2]
        observed = record[3] if len(record) > 3 else None
        lead = text.strip()
        if style_id is None or style_id < 0:
            continue
        if observed is not None and not observed.empty:
            formats.setdefault(style_id, []).append((index, bool(lead), observed))
        if not lead:
            continue
        if style_id not in counts:
            counts[style_id] = 0
            firsts[style_id] = index
            leads[style_id] = []
            markers[style_id] = []
            marker_prefixes[style_id] = []
            marker_eligible[style_id] = 0
            if style_id not in formats:
                formats[style_id] = []
            samples[style_id] = []
        counts[style_id] += 1
        leads[style_id].append(lead)
        is_automatic = (
            observed is not None
            and observed.heading_type is not None
            and observed.heading_type >= 1
        )
        if not is_automatic:
            marker_eligible[style_id] += 1
            marker = paragraph_lead_marker(lead)
            if marker:
                markers[style_id].append(marker)
                marker_prefixes[style_id].append(paragraph_lead_prefix(lead))
        bucket = samples[style_id]
        if len(bucket) < sample_limit and lead not in bucket:
            bucket.append(lead)
    return tuple(
        _style_run(
            style_id,
            counts[style_id],
            firsts[style_id],
            leads[style_id],
            markers[style_id],
            marker_prefixes[style_id],
            marker_eligible[style_id],
            formats[style_id],
            samples[style_id],
        )
        for style_id in sorted(counts, key=lambda value: firsts[value])
    )


def _style_run(
    style_id: int,
    count: int,
    first: int,
    leads: Sequence[str],
    markers: Sequence[str],
    marker_prefixes: Sequence[str],
    marker_eligible: int,
    formats: Sequence[_ObservedFormatRecord],
    samples: Sequence[str],
) -> ObservedStyleRun:
    representative = _representative_format(formats)
    representative_paragraph = _representative_paragraph(formats, representative)
    return ObservedStyleRun(
        style_id=style_id,
        paragraphs=count,
        first_paragraph=first,
        shared_lead_prefix=_dominant_lead_prefix(leads),
        lead_samples=tuple(samples),
        lead_marker=_majority(markers) or "",
        fixed_lead_prefix=_fixed_lead_prefix(marker_prefixes, marker_eligible),
        format=representative,
        representative_paragraph=(
            first if representative_paragraph is None else representative_paragraph
        ),
    )


def _representative_format(
    formats: Sequence[_ObservedFormatRecord],
) -> ObservedParagraphFormat | None:
    """Vote each observable axis using the paragraphs where it has meaning."""
    character = tuple(observed for _index, has_text, observed in formats if has_text)
    paragraph = tuple(observed for _index, _has_text, observed in formats)
    voted = ObservedParagraphFormat(
        face_name=_majority(
            tuple(observed.face_name for observed in character if observed.face_name)
        ),
        height=_present_majority(observed.height for observed in character),
        bold=_present_majority(observed.bold for observed in character),
        text_color=_present_majority(observed.text_color for observed in character),
        alignment=_present_majority(observed.alignment for observed in paragraph),
        line_spacing=_present_majority(observed.line_spacing for observed in paragraph),
        left_margin_hwpunit=_present_majority(
            observed.left_margin_hwpunit for observed in paragraph
        ),
        right_margin_hwpunit=_present_majority(
            observed.right_margin_hwpunit for observed in paragraph
        ),
        indentation_hwpunit=_present_majority(
            observed.indentation_hwpunit for observed in paragraph
        ),
        previous_spacing_hwpunit=_present_majority(
            observed.previous_spacing_hwpunit for observed in paragraph
        ),
        next_spacing_hwpunit=_present_majority(
            observed.next_spacing_hwpunit for observed in paragraph
        ),
        heading_type=_present_majority(observed.heading_type for observed in paragraph),
        heading_level=_present_majority(
            observed.heading_level for observed in paragraph
        ),
    )
    return None if voted.empty else voted


def _representative_paragraph(
    formats: Sequence[_ObservedFormatRecord],
    representative: ObservedParagraphFormat | None,
) -> int | None:
    if representative is None:
        return None
    with_text = tuple(item for item in formats if item[1])
    candidates = with_text or tuple(formats)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: _matching_axis_count(
            item[2],
            representative,
            character_meaningful=item[1],
        ),
    )[0]


def _matching_axis_count(
    observed: ObservedParagraphFormat,
    representative: ObservedParagraphFormat,
    *,
    character_meaningful: bool,
) -> int:
    character = (
        (
            (observed.face_name, representative.face_name),
            (observed.height, representative.height),
            (observed.bold, representative.bold),
            (observed.text_color, representative.text_color),
        )
        if character_meaningful
        else ()
    )
    paragraph = (
        (observed.alignment, representative.alignment),
        (observed.line_spacing, representative.line_spacing),
        (observed.left_margin_hwpunit, representative.left_margin_hwpunit),
        (observed.right_margin_hwpunit, representative.right_margin_hwpunit),
        (observed.indentation_hwpunit, representative.indentation_hwpunit),
        (
            observed.previous_spacing_hwpunit,
            representative.previous_spacing_hwpunit,
        ),
        (observed.next_spacing_hwpunit, representative.next_spacing_hwpunit),
        (observed.heading_type, representative.heading_type),
        (observed.heading_level, representative.heading_level),
    )
    return sum(
        expected is not None and actual == expected
        for actual, expected in (*character, *paragraph)
    )


type StyleUsageRecord = tuple[str, int | None] | tuple[str, int | None, int | None]


def build_document_style_usage(
    paragraphs: Iterable[StyleUsageRecord],
    *,
    complete: bool = True,
) -> DocumentStyleUsage:
    """Fold observed ``(leading text, style id[, heading type])`` into one table.

    Most-used style per shape wins; a tie goes to whichever style the document
    used first. Blank paragraphs carry no evidence and are skipped, so a run of
    empty spacer paragraphs cannot outvote the real body style.

    A shape whose winning style is the plain-body style is *not* recorded. The
    patterns match paragraph text, and ordinary prose walks into them: the
    ``decimal2`` pattern matches ``"3.5 배 증가했다"`` exactly as it matches
    ``"1.1 개요"``. When such a sentence is the only evidence, the shape's
    winner is simply the body style, and calling that a rule would hand every
    ``N.N`` heading the body style. Dropping it costs nothing — auto/body
    paragraphs resolve to the same body style through the body bucket anyway —
    and it stops the one-off sentence from outranking a real heading style.

    Two things keep the fold honest, and both were measured on a live document
    containing 225 body paragraphs:

    *An automatically numbered paragraph is not evidence about any text shape.*
    ``ReadParagraphLead`` reads text through ``InitScan``/``GetText``, which
    never returns a marker HWP draws itself, so a ``1)`` heading arrives as
    ``"조사항목"`` and a ``<표 N>`` caption as ``"공원현황"`` -- markerless by
    construction, not because they are prose. Counting them made the body
    bucket read ``그림타이틀 x15, 가) x13, 표타이틀 x12, (1) x10, 1) x9`` while
    the real 바탕글 contributed nothing, and every plain paragraph we appended
    came out as ``(그림 5.5.3-N)``. Skipping them is what makes the bucket mean
    "the document types nothing at the head of this paragraph" again.

    *A style speaks for a marker shape only if that is the shape its own
    paragraphs wear.* Grouping by ``style_id`` before tallying is what stops a
    single stray paragraph from defining a shape: one ``주) 체육관은 …`` source
    note in the ``자료`` style was the entire ``korean_paren`` bucket, so every
    ``가)`` paragraph we inserted landed on ``자료`` at the left margin. ``자료``
    is dominantly a markerless style, so it no longer speaks for ``가)``.

    The body bucket is deliberately exempt from that second rule. "No marker"
    is not a convention a style has to earn -- a paragraph that opens with
    nothing is evidence about plain text whatever else its style does -- and
    exempting it is also what keeps the prose guard below working, since a
    style whose only two samples happen to match ``decimal2`` still has to
    register as the body style for that shape to be dropped.
    """
    observed = 0
    shaped: list[tuple[str, int]] = []
    for record in paragraphs:
        text, style_id = record[0], record[1]
        heading_type = record[2] if len(record) > 2 else None
        if style_id is None or style_id < 0 or not text.strip():
            continue
        observed += 1
        if heading_type is not None and heading_type >= 1:
            continue
        shaped.append((paragraph_shape(text) or BODY_SHAPE, style_id))
    # dict keeps insertion order, and max() returns the first maximal entry,
    # so equal counts resolve to the shape or style observed first.
    per_style: dict[int, dict[str, int]] = {}
    for shape, style_id in shaped:
        shapes = per_style.setdefault(style_id, {})
        shapes[shape] = shapes.get(shape, 0) + 1
    own_shape = {
        style_id: max(shapes.items(), key=lambda item: item[1])[0]
        for style_id, shapes in per_style.items()
    }
    tallies: dict[str, dict[int, int]] = {}
    for shape, style_id in shaped:
        if shape != BODY_SHAPE and own_shape[style_id] != shape:
            continue
        counts = tallies.setdefault(shape, {})
        counts[style_id] = counts.get(style_id, 0) + 1
    winners = {
        shape: max(counts.items(), key=lambda item: item[1])[0]
        for shape, counts in tallies.items()
    }
    body_style_id = winners.get(BODY_SHAPE)
    return DocumentStyleUsage(
        shape_styles={
            shape: style_id
            for shape, style_id in winners.items()
            if shape == BODY_SHAPE or style_id != body_style_id
        },
        observed_paragraphs=observed,
        complete=complete,
    )
