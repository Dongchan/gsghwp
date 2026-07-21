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

from hwp_mcp_registry import search_tool_specs, tool_names, tool_specs  # noqa: E402


def test_production_catalog_exposes_fast_structure_and_layout_composers() -> None:
    production = tool_names("production")

    assert "hwp_inspect_page_fast" in production
    assert "hwp_inspect_structure" in production
    assert "hwp_get_capabilities" in production
    assert "hwp_search_tools" in production
    assert "hwp_append_report" in production
    assert "hwp_append_excel_table" in production


def test_tool_search_finds_excel_and_text_to_report_workflows() -> None:
    excel = search_tool_specs("엑셀 빈칸 제거 표 정리", profile="production")
    report = search_tool_specs("텍스트를 표와 글로 정리", profile="production")
    illustrated_report = search_tool_specs(
        "텍스트 표 그림 포함 보고서",
        profile="production",
    )

    assert excel[0].name == "hwp_append_excel_table"
    assert report[0].name == "hwp_append_report"
    assert illustrated_report[0].name == "hwp_append_report"


def test_tool_search_routes_diverse_document_requests_without_generic_table_bias() -> None:
    cases = (
        (
            "엑셀 건축개요와 층별 면적표의 빈 바깥 칸을 제거해 한컴 표로 정리",
            "hwp_append_excel_table",
        ),
        (
            "기존 한컴 표의 행을 늘려 여러 레코드를 채우기",
            "hwp_expand_and_fill_table",
        ),
        (
            "기존 한컴 표에 조망점 값을 채우기",
            "hwp_fill_table",
        ),
        (
            "문서 구조를 빠르게 조회해서 셀 높이와 그림 포함 여부 확인",
            "hwp_inspect_page_fast",
        ),
        ("현재 열린 한글 문서 목록 확인", "hwp_list_open_documents"),
        (
            "참고 이미지를 통이미지로 붙이지 말고 표와 글로 보고서 재생성",
            "hwp_append_report",
        ),
        (
            "PPT 내용을 두 페이지 안에 기존 한컴 양식으로 재구성",
            "hwp_append_report",
        ),
    )

    for query, expected in cases:
        matches = search_tool_specs(query, profile="production", limit=3)

        assert matches[0].name == expected


def test_tool_search_routes_common_live_edit_intents_to_concrete_tools() -> None:
    cases = (
        ("기존 표 제목 캡션을 수정해", "hwp_add_caption"),
        ("현재 문서의 표타이틀 스타일을 확인해", "hwp_list_styles"),
        ("기존 그림을 새 파일로 교체해", "hwp_replace_image"),
        ("표 셀을 두 칸으로 나눠", "hwp_split_table_cell"),
        ("표 셀 범위를 하나로 병합해", "hwp_merge_table_cells"),
        ("53페이지 자체를 삭제해", "hwp_delete_page"),
        ("방금 편집을 롤백해", "hwp_undo"),
        ("현재 문서 구조를 빠르게 조회해", "hwp_inspect_page_fast"),
        ("캡션과 병합 셀까지 상세 구조 조회해", "hwp_inspect_structure"),
        ("8쪽 다음에 새 쪽을 추가하고 표를 삽입해", "hwp_insert_layout"),
        ("현재 커서의 본문 중간에 글과 표를 추가해", "hwp_insert_layout"),
    )

    for query, expected in cases:
        matches = search_tool_specs(query, profile="production", limit=3)

        assert matches[0].name == expected


def test_every_production_tool_is_searchable_by_exact_name_and_description() -> None:
    for spec in tool_specs("production"):
        exact_name_matches = search_tool_specs(
            spec.name,
            profile="production",
            limit=3,
        )
        description_matches = search_tool_specs(
            spec.description,
            profile="production",
            limit=3,
        )

        assert exact_name_matches[0].name == spec.name
        assert spec.name in {match.name for match in description_matches}


def test_tool_search_tolerates_common_korean_typos() -> None:
    cases = (
        ("기존 그립을 새 파일로 교채해", "hwp_replace_image"),
        ("방금 실헹 취소", "hwp_undo"),
    )

    for query, expected in cases:
        matches = search_tool_specs(query, profile="production", limit=3)

        assert matches[0].name == expected


def test_tool_search_respects_negated_intents() -> None:
    cases = (
        (
            "기존 그림을 교체하지 말고 새 그림을 삽입해",
            "hwp_insert_image",
            "hwp_replace_image",
        ),
        (
            "페이지는 삭제하지 말고 방금 편집만 되돌려",
            "hwp_undo",
            "hwp_delete_page",
        ),
        (
            "표 행은 늘리지 말고 기존 셀 값만 채워",
            "hwp_fill_table",
            "hwp_expand_and_fill_table",
        ),
    )

    for query, expected, rejected in cases:
        matches = search_tool_specs(query, profile="production", limit=5)
        names = tuple(match.name for match in matches)

        assert names[0] == expected
        assert rejected not in names

    pure_negation = search_tool_specs(
        "페이지 삭제하지 마",
        profile="production",
        limit=5,
    )
    assert "hwp_delete_page" not in {match.name for match in pure_negation}


def test_tool_search_keeps_multiple_positive_intents_and_word_order() -> None:
    table_and_caption = search_tool_specs(
        "캡션도 추가하고 기존 표에는 값만 채워",
        profile="production",
        limit=5,
    )
    structure_and_styles = search_tool_specs(
        "스타일 목록과 상세 문서 구조를 함께 확인",
        profile="production",
        limit=5,
    )
    permuted = search_tool_specs(
        "입력해 셀에 기존 표 값을",
        profile="production",
        limit=3,
    )

    assert {"hwp_fill_table", "hwp_add_caption"} <= {
        match.name for match in table_and_caption
    }
    assert {"hwp_list_styles", "hwp_inspect_structure"} <= {
        match.name for match in structure_and_styles
    }
    assert permuted[0].name == "hwp_fill_table"
