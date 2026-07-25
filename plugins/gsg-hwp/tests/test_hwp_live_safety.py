from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_safety import require_writable_document, run_layout_mutation  # noqa: E402


def test_mutation_error_marks_document_unsafe_and_blocks_the_next_write() -> None:
    # Given
    selector = "document-selector"
    unsafe_selectors: set[str] = set()

    def fail_after_mutation_started() -> None:
        raise HwpLiveError("native verification failed")

    # When
    with pytest.raises(HwpLiveError, match="native verification failed"):
        run_layout_mutation(
            unsafe_selectors,
            selector,
            fail_after_mutation_started,
        )

    # Then
    assert unsafe_selectors == {selector}
    with pytest.raises(HwpLiveError, match="추가 수정"):
        require_writable_document(unsafe_selectors, selector)


def test_successful_mutation_keeps_document_writable() -> None:
    # Given
    selector = "document-selector"
    unsafe_selectors: set[str] = set()

    # When
    run_layout_mutation(unsafe_selectors, selector, lambda: None)

    # Then
    require_writable_document(unsafe_selectors, selector)
    assert unsafe_selectors == set()
