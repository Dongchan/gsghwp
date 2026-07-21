from __future__ import annotations

import gzip
import re
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, final

from hwp_errors import HwpLiveError
from hwp_operation_command import SCALAR_PARAMETER_TYPES
from hwp_operation_contract import (
    OperationCandidate,
    OperationExecutionPolicy,
    OperationInputSpec,
    OperationIndexResource,
    OperationMatchKind,
    OperationNativeExecution,
    OperationResolution,
)
from hwp_official_api_contract import (
    OfficialAction,
    OfficialApiCatalog,
    OfficialAutomationMember,
    OfficialParameterSet,
)
from hwp_workflow_hybrid import domain_concepts


_OPERATION_INDEX_PATH = (
    Path(__file__).parents[1]
    / "resources"
    / "hancom_operation_index_v1.json.gz"
)


@dataclass(frozen=True, slots=True)
class _OperationRecord:
    candidate: OperationCandidate
    tokens: frozenset[str]
    name_tokens: frozenset[str]
    concepts: frozenset[str]
    identity_concepts: frozenset[str]
    normalized_text: str


_WORD: Final = re.compile(r"[0-9A-Za-z]+|[가-힣]+|[\u3400-\u9fff]+")
_CAMEL_BOUNDARY: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KOREAN_SUFFIXES: Final = (
    "으로부터",
    "에게서",
    "으로",
    "에서",
    "에게",
    "부터",
    "까지",
    "처럼",
    "보다",
    "의",
    "을",
    "를",
    "이",
    "가",
    "은",
    "는",
    "에",
    "로",
)

_CONCEPT_GROUPS: Final[Mapping[str, tuple[str, ...]]] = {
    "begin": ("처음", "시작", "맨앞", "begin", "start"),
    "document": ("문서", "파일", "한글문서", "document", "file"),
    "embedded": ("포함", "내부데이터", "내장", "embedded", "embed"),
    "end": ("끝", "마지막", "맨뒤", "end", "last"),
    "linked": ("연결", "링크", "linked"),
    "move": ("이동", "옮겨", "보내", "가줘", "move", "go"),
    "picture": ("그림", "사진", "이미지", "picture", "photo", "image"),
    "selected": ("선택", "선택중", "selected", "selection"),
    "table": ("표", "테이블", "table"),
    "text": ("문장", "텍스트", "본문", "text"),
}
_GOAL_CONCEPTS: Final = frozenset({"begin", "embedded", "end", "move"})

_ACTION_ALIASES: Final[Mapping[str, tuple[str, ...]]] = {
    "MoveDocEnd": (
        "문서 끝",
        "문서 끝으로 이동",
        "문서 마지막으로 이동",
        "맨 끝으로 이동",
        "go to document end",
    ),
    "MoveDocBegin": (
        "문서 시작",
        "문서 처음으로 이동",
        "맨 앞으로 이동",
        "go to document start",
    ),
    "MovePageBegin": ("쪽 처음으로 이동", "페이지 처음으로 이동"),
    "MovePageEnd": ("쪽 끝으로 이동", "페이지 끝으로 이동"),
    "MoveParaBegin": ("문단 처음으로 이동",),
    "MoveParaEnd": ("문단 끝으로 이동",),
    "BreakPage": ("페이지 나누기", "쪽 나누기"),
    "BreakPara": ("문단 나누기", "새 문단"),
    "CharShapeBold": ("글자 진하게", "굵게", "bold"),
    "CharShapeItalic": ("기울임", "이탤릭", "italic"),
    "CharShapeUnderline": ("밑줄", "underline"),
    "SelectAll": ("전체 선택", "모두 선택"),
    "TableCreate": ("표 만들기", "표 생성"),
    "PictureLinkedToEmbedded": (
        "선택한 연결 그림을 문서에 포함",
        "선택한 연결 그림을 문서에 포함시켜",
        "연결 그림 포함",
        "linked picture to embedded",
    ),
    "Cancel": ("선택 취소", "취소", "escape"),
}

_KNOWN_FAILURES: Final[Mapping[str, str]] = {
    "action:SaveHistoryItem": (
        "실제 수정 문서와 VersionInfo 입력 후에도 Execute/Run이 네이티브 예외로 실패했습니다"
    ),
    "automation:IHwpObject.ExportStyle:method": (
        "정확한 Win32 TLB 슬롯과 유효 StyleTemplate wrapper/HSet 모두 0xD0000005로 실패했습니다"
    ),
    "automation:IHwpObject.ImportStyle:method": (
        "정확한 Win32 TLB 슬롯과 유효 StyleTemplate wrapper/HSet 모두 0xD0000005로 실패했습니다"
    ),
    "automation:IDHwpParameterArray.Clone:method": (
        "공식 ParameterArray 인터페이스를 런타임 객체가 제공하지 않아 E_NOINTERFACE입니다"
    ),
}
_ACTION_RECOMMENDED_TOOLS: Final[Mapping[str, str]] = {
    "TableCreate": "hwp_apply_layout",
}
_AUTOMATION_RECOMMENDED_TOOLS: Final[Mapping[tuple[str, str], str]] = {
    ("IHwpObject", "CreatePageImage"): "hwp_render_page",
    ("IHwpObject", "InsertPicture"): "hwp_apply_layout",
}

_BLOCKED_ACTIONS: Final = frozenset(
    {
        "Redo",
        "SaveHistoryItem",
        "Undo",
    }
)
_BLOCKED_PREFIXES: Final = (
    "file",
    "print",
    "mail",
    "save",
    "quit",
    "close",
)
_AUTOMATIC_PREFIXES: Final = (
    "cancel",
    "copy",
    "goto",
    "move",
    "scroll",
    "select",
)


def _stem(token: str) -> str:
    if not token or not ("가" <= token[0] <= "힣"):
        return token.casefold()
    for suffix in _KOREAN_SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix) + 1:
            return token[: -len(suffix)]
    return token


def _tokens(value: str) -> frozenset[str]:
    separated = _CAMEL_BOUNDARY.sub(" ", value)
    return frozenset(_stem(match.group(0)) for match in _WORD.finditer(separated))


def _normalized(value: str) -> str:
    return "".join(sorted(_tokens(value)))


def _concepts(value: str) -> frozenset[str]:
    compact = "".join(character.casefold() for character in value if character.isalnum())
    legacy = frozenset(
        concept
        for concept, expressions in _CONCEPT_GROUPS.items()
        if any(
            "".join(character.casefold() for character in expression if character.isalnum())
            in compact
            for expression in expressions
        )
    )
    return legacy | domain_concepts(value)


def _plain_parameter_set(value: str | None) -> str | None:
    if value is None:
        return None
    plain = value.rstrip("*").strip()
    if not plain or plain == "+":
        return None
    return plain


def _input_specs(parameter_sets: tuple[OfficialParameterSet, ...]) -> tuple[OperationInputSpec, ...]:
    seen: set[str] = set()
    result: list[OperationInputSpec] = []
    for parameter_set in parameter_sets:
        for item in parameter_set.items:
            key = item.name.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(
                OperationInputSpec(
                    name=item.name,
                    value_type=item.value_type,
                    subtype=item.subtype,
                    description=item.description,
                    supported=item.value_type in SCALAR_PARAMETER_TYPES,
                )
            )
    return tuple(result)


def _blocked_action(action: OfficialAction) -> bool:
    lowered = action.name.casefold()
    return (
        action.name in _BLOCKED_ACTIONS
        or lowered.startswith(_BLOCKED_PREFIXES)
        or "dialog" in lowered
        or "dlg" in lowered
    )


def _action_candidate(
    action: OfficialAction,
    ordinal: int,
    parameter_sets: Mapping[str, tuple[OfficialParameterSet, ...]],
) -> OperationCandidate:
    operation_id = f"action:{action.name}"
    parameter_set = _plain_parameter_set(action.parameter_set)
    matched_sets = () if parameter_set is None else parameter_sets.get(parameter_set, ())
    inputs = _input_specs(matched_sets)
    if parameter_set is None and action.parameter_set is not None:
        native_execution: OperationNativeExecution = "catalog_only"
    elif parameter_set is None:
        native_execution = "run_action"
    elif not matched_sets or any(not item.supported for item in inputs):
        native_execution = "catalog_only"
    else:
        native_execution = "parameter_action"

    if _blocked_action(action):
        policy: OperationExecutionPolicy = "blocked"
        native_execution = "blocked"
    elif native_execution == "catalog_only":
        policy = "catalog_only"
    elif action.name.casefold().startswith(_AUTOMATIC_PREFIXES):
        policy = "automatic"
    else:
        policy = "document_change"

    return OperationCandidate(
        operation_id=operation_id,
        category="action",
        ordinal=ordinal,
        name=action.name,
        description=action.description,
        parameter_set=parameter_set,
        inputs=inputs,
        execution_policy=policy,
        native_execution=native_execution,
        latest_live_status=(
            "known_failure" if operation_id in _KNOWN_FAILURES else "passed"
        ),
        known_failure_reason=_KNOWN_FAILURES.get(operation_id),
        recommended_tool=_ACTION_RECOMMENDED_TOOLS.get(action.name),
        source_document="ActionTable_2504.pdf",
        source_page_start=action.source_page,
        source_page_end=action.source_page,
    )


def _parameter_set_candidate(
    parameter_set: OfficialParameterSet,
    ordinal: int,
    duplicate: bool,
) -> OperationCandidate:
    suffix = f"@{parameter_set.source_page_start}" if duplicate else ""
    return OperationCandidate(
        operation_id=f"parameter_set:{parameter_set.name}{suffix}",
        category="parameter_set",
        ordinal=ordinal,
        name=parameter_set.name,
        description=parameter_set.description,
        inputs=_input_specs((parameter_set,)),
        execution_policy="catalog_only",
        native_execution="catalog_only",
        source_document="ParameterSetTable_2504.pdf",
        source_page_start=parameter_set.source_page_start,
        source_page_end=parameter_set.source_page_end,
    )


def _automation_candidate(
    member: OfficialAutomationMember,
    ordinal: int,
) -> OperationCandidate:
    operation_id = f"automation:{member.owner}.{member.name}:{member.member_kind}"
    return OperationCandidate(
        operation_id=operation_id,
        category="automation",
        ordinal=ordinal,
        name=member.name,
        owner=member.owner,
        member_kind=member.member_kind,
        description=member.description,
        declaration=member.declaration or None,
        execution_policy="catalog_only",
        native_execution="catalog_only",
        latest_live_status=(
            "known_failure" if operation_id in _KNOWN_FAILURES else "passed"
        ),
        known_failure_reason=_KNOWN_FAILURES.get(operation_id),
        recommended_tool=_AUTOMATION_RECOMMENDED_TOOLS.get(
            (member.owner, member.name)
        ),
        source_document="HwpAutomation_2504.pdf",
        source_page_start=member.source_page_start,
        source_page_end=member.source_page_end,
    )


def build_operation_candidates(
    catalog: OfficialApiCatalog,
) -> tuple[OperationCandidate, ...]:
    parameter_sets: defaultdict[str, list[OfficialParameterSet]] = defaultdict(list)
    for parameter_set in catalog.parameter_sets:
        parameter_sets[parameter_set.name].append(parameter_set)
    grouped = {name: tuple(values) for name, values in parameter_sets.items()}

    result: list[OperationCandidate] = []
    result.extend(
        _action_candidate(action, ordinal, grouped)
        for ordinal, action in enumerate(catalog.actions, start=1)
    )
    result.extend(
        _parameter_set_candidate(
            parameter_set,
            ordinal,
            len(grouped[parameter_set.name]) > 1,
        )
        for ordinal, parameter_set in enumerate(catalog.parameter_sets, start=1)
    )
    result.extend(
        _automation_candidate(member, ordinal)
        for ordinal, member in enumerate(catalog.automation_members, start=1)
    )
    return tuple(result)


def _category_order(candidate: OperationCandidate) -> int:
    return {"action": 0, "parameter_set": 1, "automation": 2}[candidate.category]


@final
class OperationRegistry:
    __slots__ = ("_by_id", "_concept_index", "_exact", "_records", "_token_index")

    def __init__(self, candidates: tuple[OperationCandidate, ...]) -> None:
        records: list[_OperationRecord] = []
        exact: defaultdict[str, list[int]] = defaultdict(list)
        token_index: defaultdict[str, set[int]] = defaultdict(set)
        concept_index: defaultdict[str, set[int]] = defaultdict(set)
        by_id: dict[str, int] = {}

        for candidate in candidates:
            aliases = _ACTION_ALIASES.get(candidate.name, ())
            searchable = " ".join(
                value
                for value in (
                    candidate.operation_id,
                    candidate.name,
                    candidate.owner or "",
                    candidate.parameter_set or "",
                    candidate.description,
                    candidate.declaration or "",
                    *aliases,
                )
                if value
            )
            concept_searchable = " ".join(
                value
                for value in (
                    candidate.name,
                    candidate.description,
                    *aliases,
                )
                if value
            )
            identity_searchable = " ".join((candidate.name, *aliases))
            record = _OperationRecord(
                candidate=candidate,
                tokens=_tokens(searchable),
                name_tokens=_tokens(" ".join((candidate.name, *aliases))),
                concepts=_concepts(concept_searchable),
                identity_concepts=_concepts(identity_searchable),
                normalized_text=_normalized(searchable),
            )
            index = len(records)
            records.append(record)
            by_id[candidate.operation_id.casefold()] = index
            exact[_normalized(candidate.name)].append(index)
            if candidate.owner is not None:
                exact[_normalized(f"{candidate.owner}.{candidate.name}")].append(index)
            if candidate.description and len(candidate.description) <= 500:
                exact[_normalized(candidate.description)].append(index)
            for alias in aliases:
                exact[_normalized(alias)].append(index)
            for token in record.tokens:
                token_index[token].add(index)
            for concept in record.concepts:
                concept_index[concept].add(index)

        self._records = tuple(records)
        self._by_id = by_id
        self._exact = {key: tuple(dict.fromkeys(values)) for key, values in exact.items()}
        self._token_index = {
            key: frozenset(values) for key, values in token_index.items()
        }
        self._concept_index = {
            key: frozenset(values) for key, values in concept_index.items()
        }

    @property
    def count(self) -> int:
        return len(self._records)

    def _match(
        self,
        index: int,
        confidence: float,
        kind: OperationMatchKind,
    ) -> OperationCandidate:
        return self._records[index].candidate.model_copy(
            update={"confidence": confidence, "match_kind": kind}
        )

    def _exact_resolution(
        self,
        query: str,
        started: int,
    ) -> OperationResolution | None:
        direct = self._by_id.get(query.casefold())
        if direct is not None:
            operation = self._match(direct, 1, "operation_id")
            return OperationResolution(
                query=query,
                status="resolved",
                registry_entries=self.count,
                lookup_microseconds=(time.perf_counter_ns() - started) // 1_000,
                operation=operation,
                candidates=(operation,),
            )

        normalized = _normalized(query)
        indices = self._exact.get(normalized, ())
        if not indices:
            return None
        ranked = sorted(
            indices,
            key=lambda index: (
                _category_order(self._records[index].candidate),
                self._records[index].candidate.ordinal,
            ),
        )
        action_indices = tuple(
            index
            for index in ranked
            if self._records[index].candidate.category == "action"
        )
        selected = action_indices[0] if len(action_indices) == 1 else None
        if selected is None and len(ranked) == 1:
            selected = ranked[0]
        kind: OperationMatchKind = (
            "name"
            if selected is not None
            and _normalized(self._records[selected].candidate.name) == normalized
            else "alias"
        )
        matches = tuple(self._match(index, 1, kind) for index in ranked[:24])
        return OperationResolution(
            query=query,
            status="resolved" if selected is not None else "ambiguous",
            registry_entries=self.count,
            lookup_microseconds=(time.perf_counter_ns() - started) // 1_000,
            operation=(None if selected is None else self._match(selected, 1, kind)),
            candidates=matches,
        )

    def resolve(self, query: str) -> OperationResolution:
        cleaned = query.strip()
        if not cleaned:
            raise HwpLiveError("한컴 작업 의도가 비어 있습니다")
        if len(cleaned) > 500:
            raise HwpLiveError("한컴 작업 의도는 500자 이하여야 합니다")
        started = time.perf_counter_ns()
        exact = self._exact_resolution(cleaned, started)
        if exact is not None:
            return exact

        query_tokens = _tokens(cleaned)
        query_concepts = _concepts(cleaned)
        indices: set[int] = set()
        for token in query_tokens:
            indices.update(self._token_index.get(token, ()))
        for concept in query_concepts:
            indices.update(self._concept_index.get(concept, ()))
        if not indices:
            return OperationResolution(
                query=cleaned,
                status="not_found",
                registry_entries=self.count,
                lookup_microseconds=(time.perf_counter_ns() - started) // 1_000,
                candidates=(),
            )

        normalized_query = _normalized(cleaned)
        query_goals = query_concepts & _GOAL_CONCEPTS
        ranked: list[tuple[float, int]] = []
        for index in indices:
            record = self._records[index]
            matched = query_tokens & record.tokens
            coverage = len(matched) / len(query_tokens)
            name_coverage = len(query_tokens & record.name_tokens) / len(query_tokens)
            concept_coverage = (
                len(query_concepts & record.concepts) / len(query_concepts)
                if query_concepts
                else 0
            )
            identity_coverage = (
                len(query_concepts & record.identity_concepts) / len(query_concepts)
                if query_concepts
                else 0
            )
            goal_coverage = (
                len(query_goals & record.identity_concepts) / len(query_goals)
                if query_goals
                else 0
            )
            target_penalty = (
                0.08
                if record.candidate.name.casefold().startswith("movesel")
                and not ({"selected", "selection"} & query_concepts)
                else 0.0
            )
            phrase_bonus = 0.1 if normalized_query in record.normalized_text else 0.0
            rarity = sum(
                min(1.0, 5 / len(self._token_index[token])) for token in matched
            ) / len(query_tokens)
            confidence = min(
                0.99,
                0.42 * coverage
                + 0.1 * name_coverage
                + 0.52 * concept_coverage
                + 0.08 * identity_coverage
                + 0.12 * goal_coverage
                + phrase_bonus
                + 0.06 * rarity
                - target_penalty,
            )
            ranked.append((confidence, index))
        ranked.sort(
            key=lambda item: (
                -item[0],
                _category_order(self._records[item[1]].candidate),
                self._records[item[1]].candidate.ordinal,
            )
        )
        top = ranked[:24]
        if not top or top[0][0] < 0.35:
            return OperationResolution(
                query=cleaned,
                status="not_found",
                registry_entries=self.count,
                lookup_microseconds=(time.perf_counter_ns() - started) // 1_000,
                candidates=(),
            )

        matches = tuple(self._match(index, score, "search") for score, index in top)
        margin = top[0][0] - (top[1][0] if len(top) > 1 else 0)
        runner_identity = (
            self._records[top[1][1]].identity_concepts
            if len(top) > 1
            else frozenset[str]()
        )
        top_record = self._records[top[0][1]]
        decisive_concept_match = (
            len(query_concepts & top_record.concepts) >= 3
            and bool(query_goals)
            and query_goals <= top_record.identity_concepts
            and not query_goals <= runner_identity
            and top[0][0] >= 0.6
        )
        selected = (
            top[0][1]
            if margin >= 0.08 and (top[0][0] >= 0.78 or decisive_concept_match)
            else None
        )
        return OperationResolution(
            query=cleaned,
            status="resolved" if selected is not None else "ambiguous",
            registry_entries=self.count,
            lookup_microseconds=(time.perf_counter_ns() - started) // 1_000,
            operation=(
                None
                if selected is None
                else self._match(selected, top[0][0], "search")
            ),
            candidates=matches,
        )


@lru_cache(maxsize=1)
def load_operation_index() -> OperationIndexResource:
    if not _OPERATION_INDEX_PATH.is_file():
        raise HwpLiveError(f"한컴 작업 인덱스가 없습니다: {_OPERATION_INDEX_PATH}")
    try:
        payload = gzip.decompress(_OPERATION_INDEX_PATH.read_bytes())
        resource = OperationIndexResource.model_validate_json(payload)
    except (OSError, ValueError) as error:
        raise HwpLiveError("한컴 작업 인덱스를 해석하지 못했습니다") from error
    if len(resource.entries) != 1_452:
        raise HwpLiveError("한컴 작업 인덱스 항목 수가 1,452개가 아닙니다")
    return resource


@lru_cache(maxsize=1)
def operation_registry() -> OperationRegistry:
    return OperationRegistry(load_operation_index().entries)


def resolve_operation(query: str) -> OperationResolution:
    return operation_registry().resolve(query)
