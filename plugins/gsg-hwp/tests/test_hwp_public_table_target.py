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

from hwp_operation_contract import WorkflowTargetCandidate  # noqa: E402
from hwp_public_contract import PublicTableTarget  # noqa: E402
from hwp_public_table_target import (  # noqa: E402
    PublicTableTargetStore,
    UnknownPublicTargetError,
)


def test_cache_miss_preserves_raw_id_and_caller_table_hints() -> None:
    target = PublicTableTarget(
        target_id="native-table-42",
        page=3,
        table_index=2,
        caption="분기별 실적",
        headers=("분기", "매출"),
    )

    resolved = PublicTableTargetStore().resolve(target)

    assert resolved.target.control_instance_id == "native-table-42"
    assert resolved.target.page_hint == 3
    assert resolved.target.table_index == 2
    assert resolved.target.caption_contains == "분기별 실적"
    assert resolved.target.header_signature == ("분기", "매출")


def test_cache_miss_with_only_raw_id_does_not_invent_hints() -> None:
    resolved = PublicTableTargetStore().resolve(
        PublicTableTarget(target_id="native-table-42")
    )

    assert resolved.target.control_instance_id == "native-table-42"
    assert resolved.target.page_hint is None
    assert resolved.target.table_index is None
    assert resolved.target.caption_contains is None
    assert resolved.target.header_signature == ()


def test_cache_hit_returns_stored_target_despite_conflicting_caller_hints() -> None:
    store = PublicTableTargetStore()
    target_id = store.remember(
        (WorkflowTargetCandidate(kind="table", page=2, table_index=1),),
        "C:/documents/sample.hwp",
    )[0]
    stored = store.resolve(PublicTableTarget(target_id=target_id))

    resolved = store.resolve(
        PublicTableTarget(
            target_id=target_id,
            page=9,
            table_index=8,
            caption="호출자 값",
            headers=("호출자",),
        )
    )

    assert resolved is stored
    assert resolved.target.page_hint == 2
    assert resolved.target.table_index == 1
    assert resolved.target.caption_contains is None
    assert resolved.target.header_signature == ()


def test_unknown_opaque_target_id_still_raises() -> None:
    store = PublicTableTargetStore()

    with pytest.raises(UnknownPublicTargetError) as error:
        _ = store.resolve(
            PublicTableTarget(
                target_id="hwp-target-missing",
                page=3,
                table_index=2,
            )
        )

    assert error.value.target_id == "hwp-target-missing"
