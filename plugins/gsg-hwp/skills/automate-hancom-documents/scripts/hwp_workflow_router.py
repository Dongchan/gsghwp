from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from hwp_errors import HwpLiveError
from hwp_operation_contract import (
    HwpWorkflowId,
    WorkflowCandidate,
    WorkflowExecution,
    WorkflowMatchKind,
    WorkflowResolution,
)
from hwp_workflow_semantics import (
    WorkflowSemanticFrame,
    compact_workflow_text,
    parse_workflow_semantics,
    preferred_workflow,
)
from hwp_workflow_hybrid import (
    WorkflowHybridIndex,
    WorkflowRoutingState,
    WorkflowSearchDocument,
    domain_concepts,
    workflow_state_bonus,
)
from hwp_workflow_korean_corpus import korean_workflow_corpus_text


@dataclass(frozen=True, slots=True)
class _WorkflowDefinition:
    workflow_id: HwpWorkflowId
    description: str
    execution: WorkflowExecution
    steps: tuple[str, ...]
    groups: tuple[tuple[str, ...], ...]
    contexts: tuple[str, ...] = ()
    negatives: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


_TABLE = ("표", "테이블", "셀", "행", "열", "table", "cell", "row", "column")
_IMAGE = ("그림", "이미지", "사진", "도면", "image", "picture", "photo")
_PAGE = ("페이지", "쪽", "page")
_INSPECT = ("조회", "확인", "파악", "읽어", "찾아", "검사", "inspect", "find")
_FILL = ("채우", "채워", "채움", "기입", "입력", "써", "작성", "갱신", "반영", "넣어", "fill", "populate")
_INSERT = ("삽입", "넣어", "배치", "추가", "insert", "place", "add")
_REPEAT = ("복제", "반복", "늘리", "개수", "수만큼", "복사", "repeat", "duplicate")
_CONTROL = ("개체", "컨트롤", "도형", "상자", "control", "object")


_DEFINITIONS: Final = (
    _WorkflowDefinition(
        "document.inspect_structure",
        "현재 문서나 지정 쪽의 본문·표·컨트롤 구조를 네이티브로 조회",
        "recipe",
        ("InspectStructure",),
        (("구조", "본문", "컨트롤", "머리글", "structure"), _INSPECT),
        _PAGE + _TABLE,
    ),
    _WorkflowDefinition(
        "document.navigate",
        "문서·쪽·문단의 시작 또는 끝으로 이동",
        "official",
        ("ResolveNavigation", "RunAction"),
        (("문서", "페이지", "쪽", "문단", "document", "page", "paragraph"), ("이동", "가", "move", "go")),
        ("처음", "시작", "끝", "마지막", "앞", "뒤", "begin", "end"),
    ),
    _WorkflowDefinition(
        "document.append_layout",
        "문서 끝에 표·문단·그림 레이아웃을 추가",
        "recipe",
        ("MoveDocEnd", "ApplyLayout"),
        (("문서끝", "문서맨뒤", "맨뒤", "마지막페이지", "마지막쪽", "documentend"), ("추가", "만들", "삽입", "작성", "append", "add")),
        ("표", "보고서", "양식", "레이아웃", "그림", "table", "report", "layout"),
        aliases=("문서 끝에 표 추가", "문서 마지막에 레이아웃 추가"),
    ),
    _WorkflowDefinition(
        "document.insert_layout",
        "현재 커서 또는 지정 쪽 다음에 표·문단·그림 레이아웃을 삽입",
        "recipe",
        ("ApplyLayout",),
        (
            ("현재커서", "중간", "쪽다음", "페이지다음", "afterpage"),
            ("표삽입", "글추가", "레이아웃삽입", "insertlayout"),
        ),
    ),
    _WorkflowDefinition(
        "document.rebuild",
        "문서의 지원 블록을 새 문서 구조로 재구성",
        "recipe",
        ("InspectStructure", "RebuildDocument", "VerifyStructure"),
        (("문서", "보고서", "document", "report"), ("재구성", "다시만들", "재작성", "rebuild")),
    ),
    _WorkflowDefinition(
        "document.replace_selection",
        "현재 선택 영역의 텍스트를 교체",
        "recipe",
        ("ResolveSelection", "ReplaceSelection"),
        (("선택", "선택영역", "selection"), ("교체", "바꾸", "대체", "replace")),
    ),
    _WorkflowDefinition(
        "document.page_break",
        "현재 위치에서 쪽 나누기",
        "official",
        ("BreakPage",),
        (("페이지", "쪽", "page"), ("나누", "분리", "break")),
    ),
    _WorkflowDefinition(
        "document.insert_page",
        "지정 위치에 새 페이지 구성 추가",
        "recipe",
        ("ResolvePosition", "InsertPageLayout"),
        (_PAGE, ("새페이지", "페이지추가", "쪽추가", "insertpage")),
    ),
    _WorkflowDefinition(
        "document.delete_page",
        "지정 페이지 삭제",
        "recipe",
        ("ResolvePage", "DeletePage", "VerifyPageCount"),
        (_PAGE, ("삭제", "지워", "제거", "delete", "remove")),
    ),
    _WorkflowDefinition(
        "document.undo",
        "직전 한컴 편집 작업 실행 취소",
        "recipe",
        ("ResolveHistory", "Undo", "VerifySnapshot"),
        (("실행취소", "되돌리", "롤백", "undo"),),
        ("문서", "한컴", "작업", "document"),
    ),
    _WorkflowDefinition(
        "document.redo",
        "취소한 한컴 편집 작업 다시 실행",
        "recipe",
        ("ResolveHistory", "Redo", "VerifySnapshot"),
        (("다시실행", "재실행", "되살리", "redo"),),
        ("문서", "한컴", "작업", "document"),
    ),
    _WorkflowDefinition(
        "control.delete",
        "지정한 기존 표·그림·도형 개체 삭제",
        "recipe",
        ("ResolveControl", "DeleteControl", "VerifyStructure"),
        (_CONTROL + _TABLE + _IMAGE, ("삭제", "지워", "제거", "delete", "remove")),
    ),
    _WorkflowDefinition(
        "text.insert",
        "현재 위치에 일반 텍스트 입력",
        "recipe",
        ("ResolvePosition", "InsertText"),
        (("글", "문장", "텍스트", "내용", "text"), ("입력", "삽입", "써", "추가", "insert", "type")),
        negatives=_TABLE + _IMAGE,
    ),
    _WorkflowDefinition(
        "text.replace",
        "문서 내 텍스트 검색 및 교체",
        "recipe",
        ("FindText", "ReplaceText", "VerifyText"),
        (("글", "문장", "텍스트", "문구", "text"), ("교체", "바꾸", "대체", "replace")),
        negatives=("선택",),
    ),
    _WorkflowDefinition(
        "text.format",
        "글자 또는 문단 서식 적용",
        "recipe",
        ("ResolveTextRange", "ApplyTextFormat"),
        (("글자", "문단", "텍스트", "font", "paragraph"), ("서식", "정렬", "굵게", "기울임", "format", "align")),
    ),
    _WorkflowDefinition(
        "table.inspect",
        "현재 쪽이나 지정 쪽에서 표 후보를 찾음",
        "recipe",
        ("InspectPage", "ResolveTable"),
        (_TABLE, ("몇개", "목록", "위치", "찾아", "find", "locate")),
        negatives=("채우", "채워", "입력", "사진", "이미지", "복제", "구조"),
    ),
    _WorkflowDefinition(
        "table.create",
        "현재 위치에 새 표 생성",
        "recipe",
        ("ResolvePosition", "TableCreate", "VerifyTable"),
        (_TABLE, ("생성", "만들", "새표", "create")),
        negatives=("복제", "반복", "끝", "마지막"),
    ),
    _WorkflowDefinition(
        "table.fill_existing",
        "기존 표를 찾아 레코드 또는 셀 값으로 채움",
        "recipe",
        ("ResolveTable", "MapRowsToCells", "FillCells", "VerifyStructure"),
        (_TABLE, _FILL),
        ("기존", "목록", "항목", "데이터", "조망점", "record", "data"),
        negatives=_IMAGE + _REPEAT,
    ),
    _WorkflowDefinition(
        "table.expand_and_fill",
        "기존 표의 마지막 행 서식을 필요한 만큼 확장한 뒤 레코드를 채움",
        "recipe",
        ("ResolveTable", "ExpandRows", "MapRowsToCells", "FillCells", "VerifyStructure"),
        (_TABLE, _REPEAT, _FILL),
        ("마지막행", "행부족", "레코드", "항목", "개", "expand"),
        negatives=_IMAGE,
    ),
    _WorkflowDefinition(
        "table.format",
        "표 테두리·배경·정렬 등 서식 적용",
        "recipe",
        ("ResolveTable", "ApplyTableFormat", "VerifyStructure"),
        (_TABLE, ("서식", "테두리", "배경", "정렬", "여백", "format", "border")),
    ),
    _WorkflowDefinition(
        "table.resize",
        "표 행·열·셀 크기 조절",
        "recipe",
        ("ResolveTable", "ResizeTable", "VerifyStructure"),
        (_TABLE, ("크기", "너비", "폭", "높이", "resize", "width", "height")),
    ),
    _WorkflowDefinition(
        "table.merge_cells",
        "지정 표 셀 병합",
        "official",
        ("ResolveCells", "TableMergeCell"),
        (_TABLE, ("병합", "합쳐", "merge")),
    ),
    _WorkflowDefinition(
        "table.split_cells",
        "지정 표 셀 나누기",
        "official",
        ("ResolveCells", "TableSplitCell"),
        (_TABLE, ("셀나누", "분할", "쪼개", "split")),
    ),
    _WorkflowDefinition(
        "table.repeat_template",
        "표 양식을 필요한 개수만큼 복제",
        "recipe",
        ("ResolveTable", "RepeatTemplate", "VerifyStructure"),
        (_TABLE, _REPEAT),
        ("양식", "서식", "개", "세트", "template"),
        negatives=_IMAGE + _FILL,
    ),
    _WorkflowDefinition(
        "table.propagate",
        "기준 표의 셀 값을 여러 대상 표에 전파",
        "recipe",
        ("ResolveSourceTable", "ResolveTargetTables", "PropagateCells"),
        (_TABLE, ("전파", "같게", "동일", "기준표", "propagate", "copyvalues")),
    ),
    _WorkflowDefinition(
        "table.import_data",
        "Office·CSV 등 구조화된 데이터를 기존 표에 가져옴",
        "recipe",
        ("ReadDataSource", "ResolveTable", "MapRowsToCells", "FillCells"),
        (_TABLE, ("가져오", "불러오", "엑셀", "시트", "csv", "import", "spreadsheet")),
    ),
    _WorkflowDefinition(
        "table.insert_images",
        "기존 표의 지정 셀에 그림을 일괄 삽입",
        "recipe",
        ("ResolveTable", "MapImagesToCells", "InsertImages", "VerifyStructure"),
        (_TABLE, _IMAGE, _INSERT),
        ("현황", "가시권", "폴더", "경로", "folder", "path"),
    ),
    _WorkflowDefinition(
        "table.build_series",
        "표 양식을 반복하고 내용과 그림을 함께 채우는 복합 작업",
        "recipe",
        ("ResolveTable", "RepeatTemplate", "FillCells", "InsertImages"),
        (_TABLE, _REPEAT, _FILL, _IMAGE),
        ("기준", "개수", "내용", "사진", "분석", "series"),
    ),
    _WorkflowDefinition(
        "image.insert",
        "현재 위치나 레이아웃에 그림 삽입",
        "recipe",
        ("ResolvePosition", "InsertPicture", "VerifyPicture"),
        (_IMAGE, _INSERT),
        negatives=_TABLE,
    ),
    _WorkflowDefinition(
        "image.replace",
        "선택되거나 지정된 그림 교체",
        "official",
        ("ResolvePicture", "PictureChange"),
        (_IMAGE, ("교체", "바꾸", "대체", "replace", "change")),
    ),
    _WorkflowDefinition(
        "image.resize",
        "그림 크기 또는 배치 크기 조절",
        "recipe",
        ("ResolvePicture", "ResizePicture", "VerifyPicture"),
        (_IMAGE, ("크기", "너비", "높이", "비율", "resize", "scale")),
    ),
    _WorkflowDefinition(
        "caption.add",
        "표·그림에 캡션 추가 또는 변경",
        "recipe",
        ("ResolveControl", "ApplyCaption", "VerifyCaption"),
        (("캡션", "caption"), ("추가", "입력", "변경", "달아", "add", "change")),
        _TABLE + _IMAGE,
    ),
    _WorkflowDefinition(
        "style.apply",
        "문단·표·컨트롤에 기존 스타일 적용",
        "recipe",
        ("ResolveTarget", "ResolveStyle", "ApplyStyle"),
        (("스타일", "style"), ("적용", "입혀", "apply")),
    ),
    _WorkflowDefinition(
        "style.copy",
        "한 대상의 스타일을 다른 대상에 복사",
        "recipe",
        ("ResolveSourceStyle", "ResolveTarget", "CopyStyle"),
        (("스타일", "서식", "style", "format"), ("복사", "같게", "copy")),
        ("원본", "대상", "source", "target"),
    ),
    _WorkflowDefinition(
        "hyperlink.modify",
        "선택 또는 지정한 하이퍼링크 대상 변경",
        "official",
        ("ResolveHyperlink", "ModifyHyperlink"),
        (("하이퍼링크", "링크", "hyperlink", "link"), ("변경", "수정", "바꾸", "modify", "change")),
    ),
)

_BY_ID: Final = {definition.workflow_id: definition for definition in _DEFINITIONS}
_HYBRID_INDEX: Final = WorkflowHybridIndex(
    tuple(
        WorkflowSearchDocument(
            workflow_id=definition.workflow_id,
            text=" ".join(
                (
                    definition.description,
                    *definition.steps,
                    *(term for group in definition.groups for term in group),
                    *definition.contexts,
                    *definition.aliases,
                    korean_workflow_corpus_text(definition.workflow_id),
                )
            ),
        )
        for definition in _DEFINITIONS
    )
)
_ATOMIC_ONLY = (("연결", "그림", "포함"), ("linked", "picture", "embedded"))
_RESOLUTION_MARGIN: Final = 0.04


def workflow_count() -> int:
    return len(_DEFINITIONS)


def has_decisive_workflow_lead(top_score: float, second_score: float) -> bool:
    return top_score >= 0.72 and top_score - second_score >= _RESOLUTION_MARGIN


def _hits(frame: WorkflowSemanticFrame, values: tuple[str, ...]) -> int:
    return sum(1 for value in values if frame.contains(value))


def _candidate(
    definition: _WorkflowDefinition,
    confidence: float,
    match_kind: WorkflowMatchKind,
) -> WorkflowCandidate:
    return WorkflowCandidate(
        workflow_id=definition.workflow_id,
        description=definition.description,
        steps=definition.steps,
        execution=definition.execution,
        confidence=min(1, max(0, confidence)),
        match_kind=match_kind,
    )


def _explicit_resolution(query: str, explicit: HwpWorkflowId, started: int) -> WorkflowResolution:
    definition = _BY_ID[explicit]
    candidate = _candidate(definition, 1, "explicit")
    return WorkflowResolution(
        query=query,
        status="resolved",
        lookup_microseconds=(time.perf_counter_ns() - started) // 1_000,
        workflow_id=explicit,
        candidates=(candidate,),
        steps=definition.steps,
        match_kind="explicit",
    )


def resolve_explicit_workflow(
    query: str,
    explicit: HwpWorkflowId,
) -> WorkflowResolution:
    cleaned = query.strip()
    if not cleaned:
        raise HwpLiveError("한컴 작업 의도가 비어 있습니다")
    if len(cleaned) > 500:
        raise HwpLiveError("한컴 작업 의도는 500자 이하여야 합니다")
    return _explicit_resolution(cleaned, explicit, time.perf_counter_ns())


def resolve_workflow(
    query: str,
    *,
    explicit: HwpWorkflowId | None = None,
    state: WorkflowRoutingState | None = None,
) -> WorkflowResolution:
    cleaned = query.strip()
    if not cleaned:
        raise HwpLiveError("한컴 작업 의도가 비어 있습니다")
    if len(cleaned) > 500:
        raise HwpLiveError("한컴 작업 의도는 500자 이하여야 합니다")
    started = time.perf_counter_ns()
    if explicit is not None:
        return _explicit_resolution(cleaned, explicit, started)

    frame = parse_workflow_semantics(cleaned)
    preference = preferred_workflow(frame)
    query_concepts = domain_concepts(frame.positive)
    if (
        state is not None
        and state.target_kind == "picture"
        and "replace" in query_concepts
    ):
        preference = "image.replace"
    hybrid_hits = {
        hit.workflow_id: hit
        for hit in _HYBRID_INDEX.search(frame.positive)
    }
    if (
        {"image", "linked", "embedded"} <= query_concepts
        or any(all(frame.contains(term) for term in pattern) for pattern in _ATOMIC_ONLY)
    ):
        return WorkflowResolution(
            query=cleaned,
            status="not_found",
            lookup_microseconds=(time.perf_counter_ns() - started) // 1_000,
        )

    ranked: list[tuple[float, _WorkflowDefinition, WorkflowMatchKind]] = []
    for definition in _DEFINITIONS:
        if any(frame.compact == compact_workflow_text(alias) for alias in definition.aliases):
            ranked.append((1, definition, "alias"))
            continue
        matched_groups = sum(1 for group in definition.groups if _hits(frame, group))
        hybrid_hit = hybrid_hits.get(definition.workflow_id)
        hybrid_score = 0.0 if hybrid_hit is None else hybrid_hit.score
        state_score = workflow_state_bonus(definition.workflow_id, state)
        if (
            matched_groups == 0
            and preference != definition.workflow_id
            and hybrid_score < 0.25
            and state_score <= 0
        ):
            continue
        group_score = 0.75 * matched_groups / len(definition.groups)
        context_score = min(0.15, 0.05 * _hits(frame, definition.contexts))
        negative_score = min(0.45, 0.15 * _hits(frame, definition.negatives))
        specificity = min(0.05, 0.01 * sum(_hits(frame, group) for group in definition.groups))
        semantic_score = 0.4 if preference == definition.workflow_id else 0
        matched_concepts = (
            0 if hybrid_hit is None else len(hybrid_hit.matched_concepts)
        )
        preference_adjustment = (
            0.25
            if preference == definition.workflow_id
            else (-0.15 if preference is not None else 0.0)
        )
        hybrid_confidence = max(0.0, min(
            1.0,
            hybrid_score
            + 0.08 * min(3, matched_concepts)
            + state_score
            + preference_adjustment,
        ))
        rule_confidence = max(
            0,
            group_score
            + context_score
            + specificity
            + semantic_score
            + state_score
            - negative_score,
        )
        ranked.append(
            (
                max(
                    hybrid_confidence,
                    rule_confidence,
                ),
                definition,
                "semantic",
            )
        )

    ranked.sort(key=lambda item: (-item[0], item[1].workflow_id))
    routing_concepts = query_concepts | frame.concepts
    target_only = (
        preference is None
        and bool(routing_concepts)
        and routing_concepts <= frozenset(
            {"document", "image", "page", "selection", "table", "text"}
        )
    )
    visible = tuple(
        _candidate(definition, score, match_kind)
        for score, definition, match_kind in ranked
        if score >= 0.25
        and (
            query_concepts != frozenset({"table"})
            or definition.workflow_id.startswith("table.")
        )
    )[:24]
    if not visible:
        return WorkflowResolution(
            query=cleaned,
            status="not_found",
            lookup_microseconds=(time.perf_counter_ns() - started) // 1_000,
        )

    top_score, top_definition, top_kind = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0
    unrepresented_composition = (
        {"table", "create", "fill"} <= query_concepts
        and preference is None
    )
    resolved = (
        not unrepresented_composition
        and not target_only
        and has_decisive_workflow_lead(top_score, second_score)
    )
    return WorkflowResolution(
        query=cleaned,
        status="resolved" if resolved else "ambiguous",
        lookup_microseconds=(time.perf_counter_ns() - started) // 1_000,
        workflow_id=top_definition.workflow_id if resolved else None,
        candidates=visible,
        steps=top_definition.steps if resolved else (),
        match_kind=top_kind if resolved else None,
    )
