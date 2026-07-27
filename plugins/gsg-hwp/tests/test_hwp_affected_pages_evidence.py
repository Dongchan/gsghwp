"""affected_pages 는 커서 위치가 아니라 실제로 바뀐 쪽을 보고해야 한다.

라이브 실측: 표 19개를 만들어 10~19쪽이 바뀌었는데 affected_pages 가 [19] 로 왔다.
모델이 그걸 믿으면 10~18쪽을 검토하지 않는다.
"""

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

from hwp_live_native_action_results import (  # noqa: E402
    NativePageControl,
    NativePageInspection,
    NativePosition,
)
from hwp_live_template_repeat import repeated_table_pages  # noqa: E402
from hwp_operation_contract import (  # noqa: E402
    OperationResult,
    page_growth_evidence,
)
from hwp_public_contract import (  # noqa: E402
    PublicActionResult,
    to_public_action_result,
)


def _edit_result(**overrides: object) -> OperationResult:
    fields: dict[str, object] = {
        "status": "executed",
        "changed": True,
        "query": "edit",
        "registry_entries": 1,
        "lookup_microseconds": 0,
        "message": "changed",
        "modified": True,
        "verified": True,
        "retry_safe": True,
    }
    fields.update(overrides)
    return OperationResult.model_validate(fields)


def _control(instance_id: str) -> NativePageControl:
    return NativePageControl(
        control_type="tbl",
        instance_id=instance_id,
        anchor=NativePosition(0, 0, 0),
        rows=3,
        columns=2,
    )


def _page(page: int, *instance_ids: str) -> NativePageInspection:
    return NativePageInspection(
        document_id=1,
        full_name="C:/doc.hwp",
        page=page,
        page_count=19,
        text="",
        controls=tuple(_control(value) for value in instance_ids),
    )


# --- 핵심: 여러 쪽에 걸친 편집이 그 쪽들을 전부 보고하는가 -------------------


def test_multi_page_edit_reports_every_changed_page() -> None:
    # Given: 10~19쪽에 표를 만든 작업. 커서는 마지막 19쪽에 있다.
    operation = _edit_result(
        current_page=19,
        page_count=19,
        changed_pages=tuple(range(10, 20)),
    )

    # When
    result = to_public_action_result(operation, ())

    # Then
    assert result.affected_pages == tuple(range(10, 20))


def test_changed_pages_are_sorted_and_deduplicated() -> None:
    # Given: 증거는 조회 순서대로 쌓이므로 정렬도 중복 제거도 되어 있지 않다.
    operation = _edit_result(current_page=12, changed_pages=(12, 10, 11, 10, 12))

    # When
    result = to_public_action_result(operation, ())

    # Then
    assert result.affected_pages == (10, 11, 12)


def test_single_page_edit_reports_only_that_page() -> None:
    # Given
    operation = _edit_result(current_page=5, changed_pages=(5,))

    # When
    result = to_public_action_result(operation, ())

    # Then
    assert result.affected_pages == (5,)


# --- 안전 테스트: 바뀌지 않은 작업은 절대 쪽 목록을 갖지 않는다 --------------


def test_unchanged_operation_reports_no_pages_even_with_evidence() -> None:
    # Given: 변경이 없었는데 증거 필드만 남은 최악의 경우.
    operation = _edit_result(
        changed=False,
        modified=False,
        reconcile_required=False,
        current_page=11,
        changed_pages=(10, 11, 12),
    )

    # When
    result = to_public_action_result(operation, ())

    # Then
    assert result.modified is False
    assert result.affected_pages == ()


def test_needs_input_operation_reports_no_pages() -> None:
    # Given
    operation = _edit_result(
        status="needs_input",
        changed=False,
        modified=False,
        required_inputs=("inputs.data",),
        current_page=7,
        changed_pages=(7, 8),
    )

    # When
    result = to_public_action_result(operation, ())

    # Then
    assert result.affected_pages == ()


# --- 안전 테스트: 범위를 모르는 작업은 기존 동작을 유지한다 ------------------


def test_operation_without_evidence_keeps_cursor_page() -> None:
    # Given: changed_pages 를 채우지 않은 작업은 지금처럼 커서 쪽을 보고한다.
    operation = _edit_result(current_page=7)

    # When
    result = to_public_action_result(operation, ())

    # Then
    assert operation.changed_pages == ()
    assert result.affected_pages == (7,)


def test_operation_without_cursor_or_evidence_reports_no_pages() -> None:
    # Given
    operation = _edit_result(current_page=None)

    # When
    result = to_public_action_result(operation, ())

    # Then
    assert result.affected_pages == ()


# --- 500개 상한 -------------------------------------------------------------


def test_page_evidence_over_public_limit_truncates_and_declares_it() -> None:
    # Given: 공개 스키마 상한(500)을 넘는 증거.
    evidence = tuple(range(1, 601))
    operation = _edit_result(current_page=600, page_count=600, changed_pages=evidence)

    # When
    result = to_public_action_result(operation, ())

    # Then: 조용히 잘라내면 모델은 501쪽부터를 "안 바뀐 쪽"으로 오해한다.
    assert PublicActionResult.model_fields["affected_pages"].metadata
    assert len(result.affected_pages) == 500
    assert result.affected_pages == tuple(range(1, 501))
    assert "600" in result.message
    assert "500" in result.message
    assert len(result.message) <= 4_000


def test_page_evidence_at_public_limit_is_not_truncated() -> None:
    # Given
    operation = _edit_result(current_page=500, changed_pages=tuple(range(1, 501)))

    # When
    result = to_public_action_result(operation, ())

    # Then
    assert len(result.affected_pages) == 500
    assert result.message == "changed"


# --- 공개 표면 불변: 근거 필드는 내부 전용이다 ------------------------------


def test_page_evidence_field_stays_out_of_the_public_operation_schema() -> None:
    # Given: OperationResult 는 hwp_get_operation_status 의 outputSchema 다.
    schema = OperationResult.model_json_schema()

    # When
    operation = _edit_result(current_page=3, changed_pages=(3, 4))
    serialized = operation.model_dump(mode="json")

    # Then: 스키마가 additionalProperties=false 이므로 스키마에도 응답에도 없어야 한다.
    assert schema["additionalProperties"] is False
    assert "changed_pages" not in schema["properties"]
    assert "changed_pages" not in serialized
    assert "affected_pages" not in serialized
    # 그래도 내부에서는 살아 있어야 한다.
    assert operation.changed_pages == (3, 4)


# --- 증거 생산자: 새 네이티브 왕복 없이 이미 조회한 결과에서 뽑는다 ----------


def test_repeated_table_pages_uses_pages_already_inspected() -> None:
    # Given: 표 반복 사후 검증이 이미 읽어 둔 쪽별 조회 결과.
    pages = (
        _page(9),
        _page(10, "table-a"),
        _page(11, "table-b"),
        _page(12, "table-b", "table-c"),
        _page(13, "other-table"),
    )

    # When
    resolved = repeated_table_pages(pages, ("table-a", "table-b", "table-c"))

    # Then: 복제 표가 실제로 관측된 쪽만. 9쪽과 13쪽은 근거가 없다.
    assert resolved == (10, 11, 12)


def test_repeated_table_pages_without_matches_is_empty() -> None:
    # Given
    pages = (_page(4, "unrelated"),)

    # When
    resolved = repeated_table_pages(pages, ("table-a",))

    # Then
    assert resolved == ()


def test_page_growth_evidence_reports_only_newly_created_pages() -> None:
    # Given: 9쪽 문서에 내용을 덧붙여 19쪽이 됐다.
    # When
    resolved = page_growth_evidence(
        before_page_count=9,
        after_page_count=19,
        current_page=19,
    )

    # Then: 10~19쪽은 전에 없던 쪽이다. 9쪽이 바뀌었다는 근거는 없다.
    assert resolved == tuple(range(10, 20))


def test_page_growth_evidence_keeps_cursor_page_when_nothing_grew() -> None:
    # Given
    # When
    resolved = page_growth_evidence(
        before_page_count=9,
        after_page_count=9,
        current_page=4,
    )

    # Then: 쪽 수가 그대로면 범위를 모른다. 기존 동작인 커서 쪽만 남는다.
    assert resolved == (4,)


def test_page_growth_evidence_unions_cursor_outside_the_new_range() -> None:
    # Given: 문서 중간에 삽입해 커서가 새 쪽 범위 밖에 있는 경우.
    # When
    resolved = page_growth_evidence(
        before_page_count=9,
        after_page_count=11,
        current_page=4,
    )

    # Then
    assert resolved == (4, 10, 11)
