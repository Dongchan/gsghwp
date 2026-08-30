from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from hwp_errors import HwpLiveError
from hwp_operation_contract import (
    HwpWorkflowId,
    WorkflowCandidate,
    WorkflowMatchKind,
    WorkflowResolution,
)
from hwp_operation_descriptor import OperationDescriptor, operation_descriptor
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
    groups: tuple[tuple[str, ...], ...]
    contexts: tuple[str, ...] = ()
    negatives: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


def _required_descriptor(workflow_id: HwpWorkflowId) -> OperationDescriptor:
    descriptor = operation_descriptor(workflow_id)
    if descriptor is None:
        raise HwpLiveError(f"등록되지 않은 한컴 operation descriptor입니다: {workflow_id}")
    return descriptor


def _register_workflow_definitions(
    definitions: tuple[_WorkflowDefinition, ...],
) -> tuple[_WorkflowDefinition, ...]:
    for definition in definitions:
        _required_descriptor(definition.workflow_id)
    return definitions


_TABLE = ("표", "테이블", "셀", "행", "열", "table", "cell", "row", "column")
_IMAGE = ("그림", "이미지", "사진", "도면", "image", "picture", "photo")
_PAGE = ("페이지", "쪽", "page")
_INSPECT = ("조회", "확인", "파악", "읽어", "찾아", "검사", "inspect", "find")
_FILL = ("채우", "채워", "채움", "기입", "입력", "써", "작성", "갱신", "반영", "넣어", "fill", "populate")
_INSERT = ("삽입", "넣어", "배치", "추가", "insert", "place", "add")
_REPEAT = ("복제", "반복", "늘리", "개수", "수만큼", "복사", "repeat", "duplicate")
_CONTROL = ("개체", "컨트롤", "도형", "상자", "control", "object")


_DEFINITIONS: Final = _register_workflow_definitions((
    _WorkflowDefinition(
        "document.inspect_structure",
        "현재 문서나 지정 쪽의 본문·표·컨트롤 구조를 네이티브로 조회",
        (("구조", "본문", "컨트롤", "머리글", "structure"), _INSPECT),
        _PAGE + _TABLE,
    ),
    _WorkflowDefinition(
        "document.navigate",
        "문서·쪽·문단의 시작 또는 끝으로 이동",
        (("문서", "페이지", "쪽", "문단", "document", "page", "paragraph"), ("이동", "가", "move", "go")),
        ("처음", "시작", "끝", "마지막", "앞", "뒤", "begin", "end"),
    ),
    _WorkflowDefinition(
        "document.append_layout",
        "문서 끝에 표·문단·그림 레이아웃을 추가",
        (("문서끝", "문서맨뒤", "맨뒤", "마지막페이지", "마지막쪽", "documentend"), ("추가", "만들", "삽입", "작성", "append", "add")),
        ("표", "보고서", "양식", "레이아웃", "그림", "table", "report", "layout"),
        aliases=("문서 끝에 표 추가", "문서 마지막에 레이아웃 추가"),
    ),
    _WorkflowDefinition(
        "document.insert_layout",
        "현재 커서 또는 지정 쪽 다음에 표·문단·그림 레이아웃을 삽입",
        (
            ("현재커서", "중간", "쪽다음", "페이지다음", "afterpage"),
            ("표삽입", "글추가", "레이아웃삽입", "insertlayout"),
        ),
    ),
    _WorkflowDefinition(
        "document.rebuild",
        "문서의 지원 블록을 새 문서 구조로 재구성",
        (("문서", "보고서", "document", "report"), ("재구성", "다시만들", "재작성", "rebuild")),
    ),
    _WorkflowDefinition(
        "document.replace_selection",
        "현재 선택 영역의 텍스트를 교체",
        (("선택", "선택영역", "selection"), ("교체", "바꾸", "대체", "replace")),
    ),
    _WorkflowDefinition(
        "document.page_break",
        "현재 위치에서 쪽 나누기",
        (("페이지", "쪽", "page"), ("나누", "분리", "break")),
    ),
    _WorkflowDefinition(
        "document.insert_page",
        "지정 위치에 새 페이지 구성 추가",
        (_PAGE, ("새페이지", "페이지추가", "쪽추가", "insertpage")),
    ),
    _WorkflowDefinition(
        "document.delete_page",
        "지정 페이지 삭제",
        (_PAGE, ("삭제", "지워", "제거", "delete", "remove")),
    ),
    _WorkflowDefinition(
        "document.undo",
        "직전 한컴 편집 작업 실행 취소",
        (("실행취소", "되돌리", "롤백", "undo"),),
        ("문서", "한컴", "작업", "document"),
    ),
    _WorkflowDefinition(
        "document.redo",
        "취소한 한컴 편집 작업 다시 실행",
        (("다시실행", "재실행", "되살리", "redo"),),
        ("문서", "한컴", "작업", "document"),
    ),
    _WorkflowDefinition(
        "control.delete",
        "지정한 기존 표·그림·도형 개체 삭제",
        (_CONTROL + _TABLE + _IMAGE, ("삭제", "지워", "제거", "delete", "remove")),
    ),
    _WorkflowDefinition(
        "text.insert",
        "현재 위치에 일반 텍스트 입력",
        (("글", "문장", "텍스트", "내용", "text"), ("입력", "삽입", "써", "추가", "insert", "type")),
        negatives=_TABLE + _IMAGE,
    ),
    _WorkflowDefinition(
        "text.replace",
        "문서 내 텍스트 검색 및 교체",
        (("글", "문장", "텍스트", "문구", "text"), ("교체", "바꾸", "대체", "replace")),
        negatives=("선택",),
    ),
    _WorkflowDefinition(
        "text.format",
        "글자 또는 문단 서식 적용",
        (("글자", "문단", "텍스트", "font", "paragraph"), ("서식", "정렬", "굵게", "기울임", "format", "align")),
    ),
    _WorkflowDefinition(
        "table.inspect",
        "현재 쪽이나 지정 쪽에서 표 후보를 찾음",
        (_TABLE, ("몇개", "목록", "위치", "찾아", "find", "locate")),
        negatives=("채우", "채워", "입력", "사진", "이미지", "복제", "구조"),
    ),
    _WorkflowDefinition(
        "table.create",
        "현재 위치에 새 표 생성",
        (_TABLE, ("생성", "만들", "새표", "create")),
        negatives=("복제", "반복", "끝", "마지막"),
    ),
    _WorkflowDefinition(
        "table.fill_existing",
        "기존 표를 찾아 레코드 또는 셀 값으로 채움",
        (_TABLE, _FILL),
        ("기존", "목록", "항목", "데이터", "조망점", "record", "data"),
        negatives=_IMAGE + _REPEAT,
    ),
    _WorkflowDefinition(
        "table.expand_and_fill",
        "기존 표의 마지막 행 서식을 필요한 만큼 확장한 뒤 레코드를 채움",
        (_TABLE, _REPEAT, _FILL),
        ("마지막행", "행부족", "레코드", "항목", "개", "expand"),
        negatives=_IMAGE,
    ),
    _WorkflowDefinition(
        "table.format",
        "표 테두리·배경·정렬 등 서식 적용",
        (_TABLE, ("서식", "테두리", "배경", "정렬", "여백", "format", "border")),
    ),
    _WorkflowDefinition(
        "table.resize",
        "표 행·열·셀 크기 조절",
        (_TABLE, ("크기", "너비", "폭", "높이", "resize", "width", "height")),
    ),
    _WorkflowDefinition(
        "table.merge_cells",
        "지정 표 셀 병합",
        (_TABLE, ("병합", "합쳐", "merge")),
    ),
    _WorkflowDefinition(
        "table.split_cells",
        "지정 표 셀 나누기",
        (_TABLE, ("셀나누", "분할", "쪼개", "split")),
    ),
    _WorkflowDefinition(
        "table.repeat_template",
        "표 양식을 필요한 개수만큼 복제",
        (_TABLE, _REPEAT),
        ("양식", "서식", "개", "세트", "template"),
        negatives=_IMAGE + _FILL,
    ),
    _WorkflowDefinition(
        "table.propagate",
        "기준 표의 셀 값을 여러 대상 표에 전파",
        (_TABLE, ("전파", "같게", "동일", "기준표", "propagate", "copyvalues")),
    ),
    _WorkflowDefinition(
        "table.import_data",
        "Office·CSV 등 구조화된 데이터를 기존 표에 가져옴",
        (_TABLE, ("가져오", "불러오", "엑셀", "시트", "csv", "import", "spreadsheet")),
    ),
    _WorkflowDefinition(
        "table.insert_images",
        "기존 표의 지정 셀에 그림을 일괄 삽입",
        (_TABLE, _IMAGE, _INSERT),
        ("현황", "가시권", "폴더", "경로", "folder", "path"),
    ),
    _WorkflowDefinition(
        "table.build_series",
        "표 양식을 반복하고 내용과 그림을 함께 채우는 복합 작업",
        (_TABLE, _REPEAT, _FILL, _IMAGE),
        ("기준", "개수", "내용", "사진", "분석", "series"),
    ),
    _WorkflowDefinition(
        "image.insert",
        "현재 위치나 레이아웃에 그림 삽입",
        (_IMAGE, _INSERT),
        negatives=_TABLE,
    ),
    _WorkflowDefinition(
        "image.replace",
        "선택되거나 지정된 그림 교체",
        (_IMAGE, ("교체", "바꾸", "대체", "replace", "change")),
    ),
    _WorkflowDefinition(
        "image.resize",
        "그림 자르기 또는 표 셀 사이 배치 조절",
        (
            _IMAGE,
            (
                "크기",
                "너비",
                "높이",
                "비율",
                "자르",
                "잘라",
                "크롭",
                "확대",
                "옮기",
                "이동",
                "resize",
                "scale",
                "crop",
            ),
        ),
    ),
    _WorkflowDefinition(
        "caption.add",
        "표·그림에 캡션 추가 또는 변경",
        (("캡션", "caption"), ("추가", "입력", "변경", "달아", "add", "change")),
        _TABLE + _IMAGE,
    ),
    _WorkflowDefinition(
        "style.apply",
        "문단·표·컨트롤에 기존 스타일 적용",
        (("스타일", "style"), ("적용", "입혀", "apply")),
    ),
    _WorkflowDefinition(
        "style.copy",
        "한 대상의 스타일을 다른 대상에 복사",
        (("스타일", "서식", "style", "format"), ("복사", "같게", "copy")),
        ("원본", "대상", "source", "target"),
    ),
    _WorkflowDefinition(
        "hyperlink.modify",
        "선택 또는 지정한 하이퍼링크 대상 변경",
        (("하이퍼링크", "링크", "hyperlink", "link"), ("변경", "수정", "바꾸", "modify", "change")),
    ),
))

_BY_ID: Final = {definition.workflow_id: definition for definition in _DEFINITIONS}
_HYBRID_INDEX: Final = WorkflowHybridIndex(
    tuple(
        WorkflowSearchDocument(
            workflow_id=definition.workflow_id,
            text=" ".join(
                (
                    definition.description,
                    *_required_descriptor(definition.workflow_id).routing_steps,
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
_ATOMIC_ONLY_PICTURE_CONCEPTS: Final = frozenset({"image", "linked", "embedded"})
_ATOMIC_ONLY_ABSTENTION: Final = "route_abstained:picture_link_embed_is_atomic_only"
_NO_CANDIDATE_ABSTENTION: Final = "route_abstained:no_candidate_reached_visibility"
# 확정 임계값. corpus(87건)로는 0.50~0.72 전 구간이 87/87로 동일해 이 값을
# 아래쪽으로 정당화하지 못한다. 근거가 서는 것은 상한뿐이다 — 0.75에서 1건,
# 0.80에서 4건, 0.90에서 10건이 resolved에서 ambiguous로 떨어진다.
_DECISIVE_TOP_SCORE: Final = 0.72
# 1·2위 격차 하한. corpus로는 0.0(=격차 요구 없음)과 0.04가 완전히 같은 결과라
# 이 값에는 근거가 없다. 0.15부터 2건이 ambiguous로 떨어지므로 상한만 관측된다.
_RESOLUTION_MARGIN: Final = 0.04


def workflow_count() -> int:
    return len(_DEFINITIONS)


def has_decisive_workflow_lead(top_score: float, second_score: float) -> bool:
    return (
        top_score >= _DECISIVE_TOP_SCORE
        and top_score - second_score >= _RESOLUTION_MARGIN
    )


def _hits(frame: WorkflowSemanticFrame, values: tuple[str, ...]) -> int:
    return sum(1 for value in values if frame.contains(value))


def _candidate(
    definition: _WorkflowDefinition,
    confidence: float,
    match_kind: WorkflowMatchKind,
) -> WorkflowCandidate:
    descriptor = _required_descriptor(definition.workflow_id)
    return WorkflowCandidate(
        workflow_id=definition.workflow_id,
        description=definition.description,
        steps=descriptor.routing_steps,
        execution=descriptor.execution,
        confidence=min(1, max(0, confidence)),
        match_kind=match_kind,
    )


def _explicit_resolution(query: str, explicit: HwpWorkflowId, started: int) -> WorkflowResolution:
    definition = _BY_ID.get(explicit)
    if definition is None:
        descriptor = _required_descriptor(explicit)
        candidate = WorkflowCandidate(
            workflow_id=explicit,
            description=f"{explicit} descriptor operation",
            steps=descriptor.routing_steps,
            execution=descriptor.execution,
            confidence=1,
            match_kind="explicit",
        )
        steps = descriptor.routing_steps
    else:
        candidate = _candidate(definition, 1, "explicit")
        steps = candidate.steps
    return WorkflowResolution(
        query=query,
        status="resolved",
        lookup_microseconds=(time.perf_counter_ns() - started) // 1_000,
        workflow_id=explicit,
        candidates=(candidate,),
        steps=steps,
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
    # 연결(linked)+그림(image)+포함(embedded)이 함께 오는 의도의 주인은 원자 액션
    # action:PictureLinkedToEmbedded 이고, 워크플로 35개 중에는 대응이 없다.
    # 여기서 기권하지 않으면 "연결된 그림을 내장 그림으로 바꿔줘"가
    # image.replace 를 confidence 1.0 · status=resolved 로 잡아 자동 실행 대상이
    # 된다(그림을 바꿔치우는 파괴적 오라우팅). 기권하면 호출부가 원자 레지스트리로
    # 넘겨 action:PictureLinkedToEmbedded 를 후보로 돌려준다.
    if _ATOMIC_ONLY_PICTURE_CONCEPTS <= query_concepts:
        return WorkflowResolution(
            query=cleaned,
            status="not_found",
            lookup_microseconds=(time.perf_counter_ns() - started) // 1_000,
            steps=(_ATOMIC_ONLY_ABSTENTION,),
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
        # 아래 가중치의 근거는 corpus 87건(KOREAN_WORKFLOW_EXAMPLES) 위에서 값을
        # 0으로 죽이거나 키워 본 관측이다. 판정은 세 종류로 갈린다.
        #   근거 있음  — 0으로 죽여도 키워도 정확도가 움직인다.
        #   상한만     — 0으로 해도 corpus는 멀쩡하고, 키우면 나빠진다.
        #   미확정     — 점수는 실제로 붙는데 argmax를 한 번도 바꾸지 못한다.
        # 근거 있음: 0 → 85/87, 2배 → 77/87(그중 4건은 틀린 워크플로로 확정).
        group_score = 0.75 * matched_groups / len(definition.groups)
        # 미확정: corpus 3045쌍 중 285쌍에서 실제로 붙고 상한 0.15까지 닿지만,
        # 0으로 죽여도 4배로 키워도 87/87 그대로다. group_score에 눌린다.
        context_score = min(0.15, 0.05 * _hits(frame, definition.contexts))
        # 미확정: 300쌍에서 붙고 상한 0.45까지 닿는데 0배·3배 모두 87/87.
        negative_score = min(0.45, 0.15 * _hits(frame, definition.negatives))
        # 상한만: 0으로 해도 87/87, 상한을 0.5로 열면 86/87.
        specificity = min(0.05, 0.01 * sum(_hits(frame, group) for group in definition.groups))
        # 상한만: 0으로 해도 87/87, 0.8로 키우면 1건이 틀린 워크플로로 확정된다.
        semantic_score = 0.4 if preference == definition.workflow_id else 0
        matched_concepts = (
            0 if hybrid_hit is None else len(hybrid_hit.matched_concepts)
        )
        preference_adjustment = (
            # 미확정: 0으로 죽여도 2배로 키워도 87/87. 같은 조건에 rule 쪽
            # semantic_score(0.4)가 이미 붙어 max() 비교에서 밀리는 것으로 보이나
            # 그 지배 관계를 증명하지는 못했다.
            0.25
            if preference == definition.workflow_id
            # 상한만: 0으로 해도 87/87, -0.45로 키우면 86/87.
            else (-0.15 if preference is not None else 0.0)
        )
        hybrid_confidence = max(0.0, min(
            1.0,
            hybrid_score
            # 근거 있음: 0 → 83/87, 3배 → 67/87.
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
            steps=(_NO_CANDIDATE_ABSTENTION,),
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
        steps=(
            _required_descriptor(top_definition.workflow_id).routing_steps
            if resolved
            else ()
        ),
        match_kind=top_kind if resolved else None,
    )
