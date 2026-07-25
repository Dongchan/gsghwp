from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_bridge_contract import BridgeSnapshot  # noqa: E402
from hwp_live_state_cache import HancomStateCache  # noqa: E402


def _snapshot(page: int, page_text: str = "") -> BridgeSnapshot:
    document = {
        "selector": "document-selector",
        "title": "cache-test.hwp",
        "full_name": "C:/documents/cache-test.hwp",
        "document_id": 17,
        "format": "HWP",
        "edit_mode": 1,
        "modified": False,
        "page_count": 100,
        "active": True,
        "window_handle": 170,
    }
    return BridgeSnapshot.model_validate(
        {
            "context": {
                "document": document,
                "current_page": page,
                "cursor": {"list_id": 0, "paragraph": 0, "character": 0},
                "selection": {
                    "selected": False,
                    "start_list": 0,
                    "start_paragraph": 0,
                    "start_character": 0,
                    "end_list": 0,
                    "end_paragraph": 0,
                    "end_character": 0,
                },
                "active_target": {
                    "kind": "caret",
                    "selection_mode_raw": 0,
                    "selection_mode": "none",
                    "strict_selection": False,
                    "multiple_cells": False,
                },
                "selected_text": "",
                "page_text": page_text,
                "character_style": {
                    "face_name": "함초롬바탕",
                    "height_hwpunit": 1000,
                    "bold": False,
                    "text_color": 0,
                },
                "paragraph_style": {
                    "align_type": 0,
                    "line_spacing": 160,
                    "left_margin_hwpunit": 0,
                    "right_margin_hwpunit": 0,
                    "indentation_hwpunit": 0,
                    "previous_spacing_hwpunit": 0,
                    "next_spacing_hwpunit": 0,
                },
                "page_setup": {
                    "paper_width_mm": 210,
                    "paper_height_mm": 297,
                    "landscape": 0,
                    "top_margin_mm": 20,
                    "bottom_margin_mm": 15,
                    "left_margin_mm": 30,
                    "right_margin_mm": 30,
                },
            },
            "structure": {
                "selector": document["selector"],
                "document_id": document["document_id"],
                "full_name": document["full_name"],
                "window_handle": document["window_handle"],
                "page": page,
                "page_count": document["page_count"],
                "state_token": f"{page:016d}",
                "page_text": page_text,
                "paragraphs": (),
                "controls": (),
                "tables": (),
            },
            "window": {
                "window_handle": document["window_handle"],
                "process_id": 171,
                "exists": True,
                "visible": True,
                "enabled": True,
                "foreground": True,
                "title": "한글",
                "class_name": "HwpFrame",
                "dialogs": (),
            },
        }
    )


def test_cache_evicts_the_least_recent_page_at_the_entry_limit() -> None:
    # Given
    cache = HancomStateCache(max_entries=2, max_bytes=1_000_000)
    first = _snapshot(1)
    second = _snapshot(2)
    first_state = cache.refresh(first, 0)
    second_state = cache.refresh(second, 0)
    _ = cache.refresh(first, 0)

    # When
    _ = cache.refresh(_snapshot(3), 0)
    retained_first = cache.refresh(first, 0)
    evicted_second = cache.refresh(second, 0)

    # Then
    assert retained_first.revision == first_state.revision
    assert evicted_second.revision > second_state.revision
    assert evicted_second.previous_revision == 0


def test_cache_does_not_retain_one_snapshot_over_the_byte_budget() -> None:
    # Given
    cache = HancomStateCache(max_entries=8, max_bytes=128)
    snapshot = _snapshot(1, "x" * 2_048)

    # When
    first = cache.refresh(snapshot, 0)
    second = cache.refresh(snapshot, 0)

    # Then
    assert first.snapshot == snapshot
    assert second.revision == first.revision + 1
    assert second.previous_revision == 0
