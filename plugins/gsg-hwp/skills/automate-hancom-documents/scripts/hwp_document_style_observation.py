"""Read the open document's own [leading shape -> style id] table, once.

The traversal itself is native (``LiveInspection.cpp::InspectParagraphStyles``):
walking paragraphs from Python would be one COM round trip per paragraph, which
is hundreds to thousands of crossings on a real report. This module only asks
for the result, folds it, and caches it per document.

Nothing here ever blocks an edit. A bridge that predates the command, a scan
that fails, or a response for a different document all degrade to
``EMPTY_STYLE_USAGE``, which makes the resolver behave exactly as it did before.
"""

from __future__ import annotations

import ntpath
from threading import Lock
from typing import Final

from dataclasses import replace

from hwp_document_style_usage import (
    EMPTY_STYLE_USAGE,
    DocumentStyleUsage,
    ObservedParagraphEvidence,
    ObservedParagraphFormat,
    build_document_style_usage,
    build_observed_style_runs,
)
from hwp_errors import HwpLiveError
from hwp_live_native_action_models import NativeParagraphStyle
from hwp_live_native_batch import read_native_paragraph_styles


# A document establishes its own conventions long before this many paragraphs,
# and the cap bounds the single response on very long documents.
PARAGRAPH_STYLE_SCAN_LIMIT: Final = 512
_CACHE_MAX_ENTRIES: Final = 8

_lock = Lock()
_usage_cache: dict[tuple[int, int, str], DocumentStyleUsage] = {}
_outer_cache_states: dict[
    tuple[int, int, str],
    tuple[tuple[int, str], str],
] = {}


def _cache_key(
    window_handle: int,
    document_id: int,
    full_name: str,
) -> tuple[int, int, str]:
    return (
        window_handle,
        document_id,
        ntpath.normcase(ntpath.normpath(full_name)) if full_name else "",
    )


def clear_document_style_usage_cache() -> None:
    with _lock:
        _usage_cache.clear()
        _outer_cache_states.clear()


def synchronize_document_style_usage_cache(
    process_session_identity: tuple[int, str],
    state_token: str,
    document_identity: tuple[int, int, str],
) -> None:
    """Invalidate one document when its owner or outer state changes."""
    key = _cache_key(*document_identity)
    cache_state = (process_session_identity, state_token)
    with _lock:
        if _outer_cache_states.get(key) == cache_state:
            return
        _ = _usage_cache.pop(key, None)
        if (
            key not in _outer_cache_states
            and len(_outer_cache_states) >= _CACHE_MAX_ENTRIES
        ):
            evicted_key = next(iter(_outer_cache_states))
            del _outer_cache_states[evicted_key]
            _ = _usage_cache.pop(evicted_key, None)
        _outer_cache_states[key] = cache_state


def document_style_usage_cache_size() -> int:
    """How many documents have been scanned. Exposed so tests can prove that a
    transient failure is not cached and a successful scan is not repeated."""
    with _lock:
        return len(_usage_cache)


def observed_paragraph_format(
    paragraph: NativeParagraphStyle,
) -> ObservedParagraphFormat:
    """Carry the bridge's per-paragraph reading across verbatim.

    Nothing is defaulted here. A field the bridge could not read stays ``None``
    so the write path leaves it alone instead of writing a zero the document
    never had.
    """
    return ObservedParagraphFormat(
        face_name=paragraph.face_name,
        face_name_latin=paragraph.face_name_latin,
        face_name_hanja=paragraph.face_name_hanja,
        face_name_japanese=paragraph.face_name_japanese,
        face_name_other=paragraph.face_name_other,
        face_name_symbol=paragraph.face_name_symbol,
        face_name_user=paragraph.face_name_user,
        height=paragraph.height,
        bold=paragraph.bold,
        text_color=paragraph.text_color,
        alignment=paragraph.alignment,
        line_spacing=paragraph.line_spacing,
        left_margin_hwpunit=paragraph.left_margin_hwpunit,
        right_margin_hwpunit=paragraph.right_margin_hwpunit,
        indentation_hwpunit=paragraph.indentation_hwpunit,
        previous_spacing_hwpunit=paragraph.previous_spacing_hwpunit,
        next_spacing_hwpunit=paragraph.next_spacing_hwpunit,
        heading_type=paragraph.heading_type,
        heading_level=paragraph.heading_level,
    )


def resolve_document_style_usage(
    window_handle: int,
    document_id: int,
    full_name: str,
    *,
    limit: int = PARAGRAPH_STYLE_SCAN_LIMIT,
) -> DocumentStyleUsage:
    """The document's observed style table, scanning at most once per document.

    Cached on (window, document id, path): a different document in the same
    window, or the same window reopened on another file, gets its own scan.
    Appending paragraphs does not invalidate it on purpose -- the conventions
    being learned are the ones we are about to reuse.
    """
    key = _cache_key(window_handle, document_id, full_name)
    with _lock:
        cached = _usage_cache.get(key)
    if cached is not None:
        return cached
    try:
        # Ask for the paragraphs' real appearance, not just their style id.
        # A bridge that predates it answers at the level it supports and says
        # so, so this costs nothing and never fails on an older bridge.
        scan = read_native_paragraph_styles(
            window_handle,
            limit=limit,
            character_shape=True,
            paragraph_shape=True,
            convention_detail=True,
        )
    except HwpLiveError:
        # Transient (selection could not be preserved, call failed). Do not
        # cache it, so the next edit can still learn the table.
        return EMPTY_STYLE_USAGE
    if scan is not None and scan.document_id != document_id:
        return EMPTY_STYLE_USAGE
    if scan is None:
        usage = EMPTY_STYLE_USAGE
    else:
        paragraph_evidence = tuple(
            ObservedParagraphEvidence(
                paragraph=paragraph.paragraph,
                lead_text=paragraph.lead_text,
                style_id=paragraph.style_id,
                format=observed_paragraph_format(paragraph),
                tail_text=paragraph.tail_text,
                text_length=paragraph.text_length,
                text_complete=paragraph.text_complete,
            )
            for paragraph in scan.paragraphs
        )
        paragraph_text_complete = all(
            paragraph.text_complete for paragraph in paragraph_evidence
        )
        usage = replace(
            build_document_style_usage(
                (
                    # HeadingType travels with the pair: a paragraph HWP
                    # numbers itself reaches us with its marker missing from
                    # the text, and only this field says so.
                    (
                        paragraph.lead_text,
                        paragraph.style_id,
                        paragraph.format.heading_type,
                    )
                    for paragraph in paragraph_evidence
                ),
                complete=scan.complete and paragraph_text_complete,
            ),
            scanned_paragraphs=len(scan.paragraphs),
            list_id=scan.list_id,
            # Same single scan, folded a second way. The shape table is what
            # the resolver applies; this is what a caller can read to see the
            # document's own conventions instead of ours.
            observed_styles=build_observed_style_runs(
                (
                    (
                        paragraph.paragraph,
                        paragraph.lead_text,
                        paragraph.style_id,
                        paragraph.format,
                    )
                    for paragraph in paragraph_evidence
                )
            ),
            paragraph_evidence=paragraph_evidence,
        )
    with _lock:
        if len(_usage_cache) >= _CACHE_MAX_ENTRIES:
            _ = _usage_cache.pop(next(iter(_usage_cache)), None)
        _usage_cache[key] = usage
    return usage
