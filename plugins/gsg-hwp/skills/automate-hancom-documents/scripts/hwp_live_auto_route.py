from __future__ import annotations

from dataclasses import dataclass
from typing import Final, assert_never

from pydantic import TypeAdapter, ValidationError

from hwp_live_operation_recipe import is_document_end_layout_intent
from hwp_live_session_workflow import (
    WorkflowPreflightInputs,
    explicit_workflow_preflight,
    workflow_result,
)
from hwp_operation_certification import certified_atomic_action, certified_recipe
from hwp_operation_contract import (
    OperationMatchKind,
    OperationResolution,
    OperationResult,
    WorkflowMatchKind,
    WorkflowResolution,
)
from hwp_operation_route_contract import HwpWorkflowId, OperationRouteSource
from hwp_workflow_hybrid import WorkflowRoutingState
from hwp_workflow_korean_corpus import KOREAN_WORKFLOW_EXAMPLES
from hwp_workflow_router import resolve_explicit_workflow, resolve_workflow
from hwp_workflow_semantics import compact_workflow_text


_WORKFLOW_ID_ADAPTER: Final[TypeAdapter[HwpWorkflowId]] = TypeAdapter(HwpWorkflowId)


@dataclass(frozen=True, slots=True)
class NaturalWorkflowRoute:
    resolution: WorkflowResolution
    source: OperationRouteSource


@dataclass(frozen=True, slots=True)
class AutomaticOperationSelection:
    operation_id: str
    source: OperationRouteSource
    executable: bool = True


@dataclass(frozen=True, slots=True)
class PreparedNaturalRoute:
    route: NaturalWorkflowRoute | None
    workflow: HwpWorkflowId | None
    selection: AutomaticOperationSelection | None
    blocked_result: OperationResult | None

    def complete(self, result: OperationResult) -> OperationResult:
        return complete_natural_route(result, self.route, self.selection)

    def unsupported_dispatch_result(
        self,
        resolution: WorkflowResolution,
    ) -> OperationResult:
        return self.complete(
            workflow_result(
                resolution,
                "unsupported",
                "인증 recipe의 기존 dispatcher 실행 경로를 찾지 못했습니다",
            )
        )


def _canonical_operation_id(query: str) -> HwpWorkflowId | None:
    try:
        return _WORKFLOW_ID_ADAPTER.validate_python(query.strip())
    except ValidationError:
        return None


def _deterministic_corpus_operation(query: str) -> HwpWorkflowId | None:
    compact = compact_workflow_text(query)
    matches: list[HwpWorkflowId] = []
    for example in KOREAN_WORKFLOW_EXAMPLES:
        expected = example.expected
        if (
            expected is not None
            and compact_workflow_text(example.query) == compact
            and expected not in matches
        ):
            matches.append(expected)
    return matches[0] if len(matches) == 1 else None


def _bounded(resolution: WorkflowResolution) -> WorkflowResolution:
    return resolution.model_copy(update={"candidates": resolution.candidates[:3]})


def _exact_route(
    query: str,
    workflow: HwpWorkflowId,
    source: OperationRouteSource,
) -> NaturalWorkflowRoute:
    return NaturalWorkflowRoute(
        _bounded(resolve_explicit_workflow(query, workflow)),
        source,
    )


def _route_source(match_kind: WorkflowMatchKind | None) -> OperationRouteSource:
    match match_kind:  # noqa: MATCH_OK
        case "alias":
            return "exact_alias"
        case "explicit":
            return "canonical_operation_id"
        case "semantic" | None:
            return "semantic"
    assert_never(match_kind)


def _atomic_route_source(match_kind: OperationMatchKind) -> OperationRouteSource:
    match match_kind:  # noqa: MATCH_OK
        case "operation_id":
            return "canonical_operation_id"
        case "name" | "alias":
            return "exact_alias"
        case "search":
            return "semantic"
    assert_never(match_kind)


def _is_deterministic(source: OperationRouteSource) -> bool:
    match source:  # noqa: MATCH_OK
        case "canonical_operation_id" | "exact_alias" | "deterministic_corpus":
            return True
        case "semantic":
            return False
    assert_never(source)


def automatic_atomic_selection(
    resolution: OperationResolution | None,
) -> AutomaticOperationSelection | None:
    if resolution is None or resolution.status != "resolved":
        return None
    operation = resolution.operation
    if operation is None or operation.match_kind == "search":
        return None
    if not resolution.candidates:
        return None
    if resolution.candidates[0].operation_id != operation.operation_id:
        return None
    executable = certified_atomic_action(operation) is not None
    diagnostic_only = operation.latest_live_status == "known_failure" or operation.execution_policy in {"blocked", "catalog_only"}
    if not executable and not diagnostic_only:
        return None
    return AutomaticOperationSelection(
        operation.operation_id,
        _atomic_route_source(operation.match_kind),
        executable,
    )


def resolve_conservative_route(
    query: str,
    state: WorkflowRoutingState,
) -> NaturalWorkflowRoute:
    canonical = _canonical_operation_id(query)
    if canonical is not None:
        return _exact_route(query, canonical, "canonical_operation_id")
    if is_document_end_layout_intent(query):
        return _exact_route(query, "document.append_layout", "exact_alias")
    corpus = _deterministic_corpus_operation(query)
    if corpus is not None:
        return _exact_route(query, corpus, "deterministic_corpus")
    resolution = _bounded(resolve_workflow(query, state=state))
    return NaturalWorkflowRoute(resolution, _route_source(resolution.match_kind))


def automatic_workflow(route: NaturalWorkflowRoute) -> HwpWorkflowId | None:
    if not _is_deterministic(route.source):
        return None
    resolution = route.resolution
    if resolution.status != "resolved" or resolution.workflow_id is None:
        return None
    if not resolution.candidates:
        return None
    if resolution.candidates[0].workflow_id != resolution.workflow_id:
        return None
    return (
        resolution.workflow_id
        if certified_recipe(resolution.workflow_id) is not None
        else None
    )


def route_candidate_result(
    route: NaturalWorkflowRoute,
    automatic: HwpWorkflowId | None,
) -> OperationResult | None:
    if automatic is not None:
        return None
    resolution = route.resolution
    match resolution.status:  # noqa: MATCH_OK
        case "resolved":
            return complete_natural_route(workflow_result(
                resolution,
                "needs_input",
                "자동 실행 조건을 충족하지 않았습니다. 반환된 operation을 inputs.operation에 전달하세요",
                required_inputs=("inputs.operation",),
            ), route, None)
        case "ambiguous":
            return complete_natural_route(workflow_result(
                resolution,
                "ambiguous",
                "후보가 근접하여 자동 실행하지 않았습니다. operation을 지정하세요",
            ), route, None)
        case "not_found":
            return None
        case "schema_conflict":
            return complete_natural_route(workflow_result(
                resolution,
                "schema_conflict",
                "구조화된 라우팅 필드가 서로 충돌합니다",
            ), route, None)
    assert_never(resolution.status)


def atomic_candidate_result(
    route: NaturalWorkflowRoute,
    resolution: OperationResolution | None,
) -> OperationResult | None:
    if resolution is None:
        return None
    match resolution.status:  # noqa: MATCH_OK
        case "resolved":
            result = workflow_result(
                route.resolution,
                "needs_input",
                "검색 후보만으로 자동 실행하지 않았습니다. operation을 지정하세요",
                required_inputs=("inputs.operation",),
            )
        case "ambiguous":
            result = workflow_result(
                route.resolution,
                "ambiguous",
                "검색 후보가 근접하여 자동 실행하지 않았습니다. operation을 지정하세요",
            )
        case "not_found":
            return None
    return complete_natural_route(result, route, None).model_copy(
        update={"candidates": resolution.candidates[:3]}
    )


def complete_natural_route(
    result: OperationResult,
    route: NaturalWorkflowRoute | None,
    selection: AutomaticOperationSelection | None,
) -> OperationResult:
    if route is None:
        return result
    selected = selection.operation_id if result.status == "executed" and selection and selection.executable else None
    return result.model_copy(
        update={
            "route_source": route.source if selection is None else selection.source,
            "auto_selected": selected is not None,
            "selected_operation": selected,
            "candidates": result.candidates[:3],
            "workflow_candidates": route.resolution.candidates,
        }
    )


def prepare_natural_route(
    route: NaturalWorkflowRoute | None,
    inputs: WorkflowPreflightInputs,
    atomic_resolution: OperationResolution | None,
) -> PreparedNaturalRoute:
    if route is None:
        return PreparedNaturalRoute(None, None, None, None)
    workflow = automatic_workflow(route)
    if workflow is None:
        atomic_selection = automatic_atomic_selection(atomic_resolution)
        if atomic_selection is not None:
            return PreparedNaturalRoute(route, None, atomic_selection, None)
    candidate_result = route_candidate_result(route, workflow)
    if candidate_result is None and workflow is None:
        candidate_result = atomic_candidate_result(route, atomic_resolution)
    if candidate_result is not None:
        return PreparedNaturalRoute(route, None, None, candidate_result)
    if workflow is None:
        return PreparedNaturalRoute(route, None, None, None)
    selection = AutomaticOperationSelection(workflow, route.source)
    preflight = explicit_workflow_preflight(route.resolution, inputs)
    blocked_result = None if preflight is None else complete_natural_route(preflight, route, selection)
    return PreparedNaturalRoute(route, workflow, selection, blocked_result)
