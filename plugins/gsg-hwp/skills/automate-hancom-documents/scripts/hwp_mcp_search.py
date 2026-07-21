from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from types import MappingProxyType
from typing import Final, Protocol, TypeVar


class SearchableTool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...


SearchableToolT = TypeVar("SearchableToolT", bound=SearchableTool)

_SUFFIXES: Final = (
    "으로",
    "에서",
    "에게",
    "까지",
    "부터",
    "처럼",
    "보다",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "와",
    "과",
    "로",
    "에",
    "의",
    "도",
    "만",
)
_VERB_SUFFIXES: Final = (
    "해주세요",
    "해줘요",
    "합니다",
    "하도록",
    "해줘",
    "해서",
    "하고",
    "하기",
    "했다",
    "해",
)
_NEGATION_MARKER: Final = re.compile(
    r"(?:하지\s*말고|하지\s*마(?:세요|라|요)?|지\s*말고|지\s*마(?:세요|라|요)?|"
    r"하지\s*않고|지\s*않고|말고|제외(?:하고|해|하여)?|금지(?:하고|해|하여)?)"
)
_INTENT_ALIASES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "hwp_list_open_documents": (
            "열린 문서 목록",
            "열린 한글 문서",
            "열린 HWP",
            "문서 목록 확인",
        ),
        "hwp_connect": ("활성 문서 연결", "한컴 문서 연결", "세션 연결"),
        "hwp_inspect": (
            "현재 쪽 확인",
            "커서와 선택 확인",
            "문서 상태 확인",
            "용지 모양 확인",
        ),
        "hwp_inspect_page_fast": (
            "빠른 구조 조회",
            "구조 조회",
            "현재 문서 구조",
            "셀 높이",
            "셀 간격",
            "그림 포함 여부",
            "렌더 말고",
        ),
        "hwp_inspect_structure": (
            "상세 구조 조회",
            "캡션과 병합 셀",
            "문단 표 셀 병합 그림 캡션",
        ),
        "hwp_list_styles": (
            "문서 스타일 확인",
            "스타일 목록",
            "표타이틀 스타일",
            "그림타이틀 스타일",
        ),
        "hwp_fill_table": (
            "기존 표 채우기",
            "표 값 채우기",
            "표 데이터 입력",
            "셀 값 입력",
            "기존 셀 값 채워",
        ),
        "hwp_expand_and_fill_table": (
            "행 추가",
            "행 늘려",
            "기존 표 행 늘려",
            "표 행 확장",
            "레코드 추가",
        ),
        "hwp_sync_visibility_analysis_tables": (
            "예비조망점 선정표",
            "가시권 분석표",
            "분석표 동기화",
            "사진 매칭",
        ),
        "hwp_fill_table_images": ("표 안 그림", "표 셀 그림", "셀에 사진"),
        "hwp_repeat_table_template": (
            "표 양식 반복",
            "빈 표 복제",
            "같은 표 여러 개",
        ),
        "hwp_insert_image": (
            "새 그림 삽입",
            "이미지 추가",
            "사진 넣기",
            "문서 끝 그림",
        ),
        "hwp_replace_image": (
            "기존 그림 교체",
            "기존 이미지 교체",
            "그림 바꾸기",
            "사진 교체",
        ),
        "hwp_add_caption": (
            "캡션 추가",
            "캡션 수정",
            "캡션 갱신",
            "표 제목",
            "그림 제목",
        ),
        "hwp_build_table_series": (
            "표 반복하고 값 그림 채우기",
            "데이터 포함 표 시리즈",
            "캡션 번호 증가 표 복제",
        ),
        "hwp_merge_table_cells": (
            "표 셀 병합",
            "셀 범위 병합",
            "셀을 하나로",
        ),
        "hwp_split_table_cell": (
            "표 셀 나누기",
            "셀 나누기",
            "셀을 두 칸",
        ),
        "hwp_delete_page": (
            "페이지 삭제",
            "쪽 삭제",
            "빈 페이지 제거",
            "빈 쪽 제거",
        ),
        "hwp_delete_control": (
            "개체 삭제",
            "컨트롤 삭제",
            "독립 표 삭제",
            "잘못 만든 표 제거",
        ),
        "hwp_undo": (
            "실행 취소",
            "되돌리기",
            "롤백",
            "방금 작업 취소",
        ),
        "hwp_redo": (
            "다시 실행",
            "재실행",
            "되살리기",
        ),
        "hwp_apply_style": ("문서 스타일 적용", "스타일 ID 적용"),
        "hwp_format_text": (
            "글자 서식",
            "문단 서식",
            "글꼴 크기 색상 정렬",
        ),
        "hwp_replace_selected_text": (
            "드래그 선택 텍스트 변경",
            "선택 영역 글 교체",
            "선택한 문장 바꾸기",
        ),
        "hwp_format_table": (
            "표 셀 서식",
            "셀 배경 테두리",
            "표 글자 정렬",
        ),
        "hwp_append_report": (
            "텍스트 보고서",
            "표와 글",
            "이미지 보고 재생성",
            "PPT 내용 재구성",
            "보고서 양식",
            "기존 양식 재구성",
        ),
        "hwp_append_excel_table": (
            "엑셀",
            "Excel",
            "XLSX",
            "빈 바깥 칸",
            "병합 테두리 행 높이",
        ),
        "hwp_append_layout": (
            "문서 끝 레이아웃",
            "문단 표 그림 추가",
            "편집 가능한 블록 추가",
        ),
        "hwp_insert_layout": (
            "현재 커서 표 삽입",
            "현재 커서의 본문 중간에 글과 표를 추가",
            "본문 중간 표 삽입",
            "내용 중간 글 추가",
            "8쪽 다음 쪽 추가",
            "특정 페이지 다음 새 페이지",
            "이미지를 편집 가능한 표로",
        ),
        "hwp_save_reopen_verify": (
            "저장 후 다시 열기",
            "저장 재개방 검증",
        ),
        "hwp_disconnect": ("문서 연결 해제", "세션 해제"),
        "hwp_get_capabilities": ("도구 기능 목록", "MCP 기능 확인"),
        "hwp_search_tools": ("도구 검색", "작업 도구 찾기"),
        "hwp_runtime_info": (
            "MCP 런타임 정보",
            "서버 PID 버전",
            "소스 스키마 해시",
        ),
        "hwp_execute": ("도구 이름으로 전달 실행", "새 도구 전달 호출"),
    }
)


def _search_terms(value: str) -> frozenset[str]:
    terms: set[str] = set()
    raw_terms: list[str] = re.findall(r"[0-9a-zA-Z가-힣_]+", value.casefold())
    for raw in raw_terms:
        terms.add(raw)
        numbered_term = re.fullmatch(r"[0-9]+([a-zA-Z가-힣_]+)", raw)
        if numbered_term is not None:
            terms.add(numbered_term.group(1))
        for suffix in _SUFFIXES:
            if raw.endswith(suffix) and len(raw) > len(suffix):
                terms.add(raw[: -len(suffix)])
                break
        for suffix in _VERB_SUFFIXES:
            if raw.endswith(suffix) and len(raw) > len(suffix):
                terms.add(raw[: -len(suffix)])
                break
    return frozenset(term for term in terms if term)


def _compact(value: str) -> str:
    return "".join(re.findall(r"[0-9a-zA-Z가-힣_]+", value.casefold()))


def _similar_term(left: str, right: str) -> bool:
    if left == right:
        return True
    shortest = min(len(left), len(right))
    if shortest < 2:
        return False
    if left in right or right in left:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right) == 2:
        return left[0] == right[0] and sum(
            left_character != right_character
            for left_character, right_character in zip(left, right, strict=True)
        ) == 1
    threshold = 0.78
    return SequenceMatcher(None, left, right).ratio() >= threshold


def _matched_term_count(
    query_terms: frozenset[str],
    candidate_terms: frozenset[str],
) -> tuple[int, int]:
    exact = len(query_terms.intersection(candidate_terms))
    unmatched = query_terms.difference(candidate_terms)
    fuzzy = sum(
        any(_similar_term(term, candidate) for candidate in candidate_terms)
        for term in unmatched
    )
    return exact, fuzzy


def _intent_score(query: str, query_terms: frozenset[str], name: str) -> int:
    query_compact = _compact(query)
    scores = [0]
    for alias in _INTENT_ALIASES.get(name, ()):
        alias_terms = _search_terms(alias)
        exact, fuzzy = _matched_term_count(alias_terms, query_terms)
        matched = exact + fuzzy
        if _compact(alias) in query_compact:
            scores.append(40 + 5 * len(alias_terms))
        elif alias_terms and matched == len(alias_terms):
            scores.append(30 + 5 * exact + 3 * fuzzy)
        elif len(alias_terms) > 1 and matched * 3 >= len(alias_terms) * 2:
            scores.append(12 + 4 * exact + 2 * fuzzy)
    return max(scores)


def _negative_intent_score(query: str, name: str) -> int:
    terms = _search_terms(query)
    if not terms:
        return 0
    return _intent_score(query, terms, name)


def _split_query_polarity(query: str) -> tuple[str, tuple[str, ...]]:
    remaining = query
    negatives: list[str] = []
    while match := _NEGATION_MARKER.search(remaining):
        negative = remaining[: match.start()].strip(" ,.;")
        if negative:
            negatives.append(negative)
        remaining = remaining[match.end() :].strip(" ,.;")
    return remaining, tuple(negatives)


def _text_score(query_terms: frozenset[str], name: str, description: str) -> int:
    name_terms = _search_terms(name.replace("_", " "))
    description_terms = _search_terms(description)
    score = 0
    for term in query_terms:
        if term in name_terms or term in name:
            score += 10
        elif term in description_terms or term in description:
            score += 4
        elif any(_similar_term(term, candidate) for candidate in name_terms):
            score += 5
        elif any(_similar_term(term, candidate) for candidate in description_terms):
            score += 2
    return score


def rank_tool_specs(
    query: str,
    specs: Sequence[SearchableToolT],
    *,
    limit: int,
) -> tuple[SearchableToolT, ...]:
    positive_query, negative_queries = _split_query_polarity(query)
    terms = _search_terms(positive_query)
    if not terms:
        return ()
    ranked: list[tuple[int, SearchableToolT]] = []
    for spec in specs:
        name = spec.name.casefold()
        description = spec.description.casefold()
        exact_identity = _compact(query) in {_compact(name), _compact(description)}
        if not exact_identity and any(
            _negative_intent_score(negative, spec.name) >= 24
            for negative in negative_queries
        ):
            continue
        if exact_identity:
            score = 2_000
        elif _compact(positive_query) in {_compact(name), _compact(description)}:
            score = 1_000
        else:
            score = _intent_score(positive_query, terms, spec.name) + _text_score(
                terms,
                name,
                description,
            )
        if score:
            ranked.append((score, spec))
    ranked.sort(key=lambda item: (-item[0], item[1].name))
    return tuple(spec for _, spec in ranked[:limit])
