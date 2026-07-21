from __future__ import annotations

from collections.abc import Mapping

from hwp_errors import HwpLiveError
from hwp_live_contract import LayoutPlan
from hwp_live_auto_route import (
    prepare_natural_route,
    resolve_conservative_route,
)
from hwp_live_native_batch import execute_native_lifecycle
from hwp_live_operation import operate_validated
from hwp_live_operation_recipe import (
    is_document_end_layout_intent,
    operate_layout,
)
from hwp_live_session_data import LiveHwpDataSession
from hwp_live_session_lifecycle import lifecycle_preflight_result, lifecycle_result
from hwp_live_session_recipe_dispatch import operate_resolved_recipe
from hwp_live_session_routing import read_operation_routing_context
from hwp_live_session_table_fill import operate_table_fill
from hwp_live_session_workflow import (
    WorkflowPreflightInputs,
    explicit_workflow_preflight,
    workflow_policy,
    workflow_result,
)
from hwp_live_structure_contract import DocumentStructure
from hwp_operation_contract import (
    HwpOperateAssets,
    HwpOperateData,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    HwpWorkflowId,
    OperationInputValue,
    OperationResult,
    OperationRoutingContext,
)
from hwp_operation_registry import resolve_operation
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs
from hwp_workflow_hybrid import WorkflowRoutingState
from hwp_workflow_router import resolve_explicit_workflow


class LiveHwpOperationSession(LiveHwpDataSession):
    __slots__: tuple[str, ...] = ()
    _last_routing_context: OperationRoutingContext | None
    _structure_snapshot: DocumentStructure | None

    def operate(
        self,
        session_id: str,
        intent_or_operation_id: str,
        inputs: Mapping[str, OperationInputValue],
        *,
        resolve_only: bool,
        allow_document_change: bool,
        use_defaults: bool,
        expected_cursor: tuple[int, int, int] | None,
        workflow: HwpWorkflowId | None = None,
        target: HwpOperateTarget | None = None,
        data: HwpOperateData | None = None,
        assets: HwpOperateAssets | None = None,
        policy: HwpOperatePolicy | None = None,
        postconditions: HwpOperatePostconditions | None = None,
        layout: LayoutPlan | None = None,
        recipe: HwpPriorityRecipeInputs | None = None,
    ) -> OperationResult:
        candidate, hwp = self._validate(session_id)
        self._last_routing_context = None
        routing_page, routing_context = read_operation_routing_context(
            candidate,
            self._routing_context_reader,
            None if target is None else target.page_hint,
        )
        self._last_routing_context = routing_context
        if workflow == "document.save_reopen_verify":
            preflight = lifecycle_preflight_result(
                intent_or_operation_id,
                resolve_only=resolve_only,
                allow_document_change=allow_document_change,
            )
            if preflight is not None:
                return preflight
            native = execute_native_lifecycle(candidate.window_handle)
            if native is None:
                raise HwpLiveError(
                    "한컴 네이티브 저장·재개방 검증기를 사용할 수 없습니다"
                )
            return lifecycle_result(intent_or_operation_id, native)
        guard = self._guard(candidate, hwp)
        natural_route = None
        if workflow is None:
            natural_route = resolve_conservative_route(
                intent_or_operation_id,
                WorkflowRoutingState(
                    table_count=routing_context.table_count,
                    picture_count=routing_context.picture_count,
                    target_kind=None if target is None else target.kind,
                    current_page=routing_context.page,
                    page_count=routing_context.page_count,
                ),
            )
            resolution = natural_route.resolution
        else:
            resolution = resolve_explicit_workflow(intent_or_operation_id, workflow)
        preflight_inputs = WorkflowPreflightInputs(target, data, assets, inputs, layout, recipe)
        if workflow is not None and resolution.status == "schema_conflict":
            return workflow_result(
                resolution,
                "schema_conflict",
                "구조화 operation과 source intent가 충돌합니다. operation 또는 구조화 입력을 수정하세요",
            )
        if workflow is not None:
            preflight = explicit_workflow_preflight(resolution, preflight_inputs)
            if preflight is not None:
                return preflight
        atomic_resolution = resolve_operation(intent_or_operation_id) if workflow is None else None
        natural_execution = prepare_natural_route(
            natural_route,
            preflight_inputs,
            atomic_resolution,
        )
        if natural_execution.blocked_result is not None:
            return natural_execution.blocked_result
        atomic_rescue = natural_execution.workflow is None and natural_execution.selection is not None
        if (
            resolution.status == "resolved"
            and resolution.workflow_id == "document.inspect_structure"
        ):
            result = workflow_result(
                resolution,
                "executed",
                "프로토콜 9 C++/ATL 네이티브 빠른 구조 조회 결과를 반환했습니다",
            ).model_copy(
                update={
                    "execution_mode": "native_in_process",
                    "native_protocol": 9,
                    "verification": "native_routing_context",
                    "verified": True,
                    "commands_executed": 1,
                    "native_elapsed_microseconds": (
                        routing_context.native_elapsed_microseconds
                    ),
                    "current_page": routing_context.page,
                    "page_count": routing_context.page_count,
                }
            )
            return natural_execution.complete(result)
        effective_policy, effective_postconditions, policy_result = workflow_policy(
            resolution,
            policy,
            postconditions,
        )
        if policy_result is not None:
            return natural_execution.complete(policy_result)
        if (
            resolution.status == "resolved"
            and resolution.workflow_id
            in {"document.append_layout", "document.insert_layout"}
        ) or (
            workflow is None and is_document_end_layout_intent(intent_or_operation_id)
        ):
            result = operate_layout(
                candidate,
                intent_or_operation_id,
                layout,
                resolve_only=resolve_only,
                allow_document_change=allow_document_change,
                expected_cursor=expected_cursor,
                unsafe_selectors=self._unsafe_selectors,
                guard=guard,
                atomic=effective_policy.atomic,
            )
            recipe_id = (
                "recipe:page.append_from_template.v1"
                if layout is None or layout.target == "document_end"
                else "recipe:document.insert_layout.v1"
            )
            return natural_execution.complete(
                result.model_copy(
                    update={
                        "recipe_id": recipe_id,
                        "workflow_candidates": resolution.candidates,
                        "required_inputs": (
                            ("inputs.layout",) if result.status == "needs_input" else ()
                        ),
                    }
                )
            )
        table_result, snapshot = operate_table_fill(
            candidate,
            hwp,
            resolution,
            target,
            data,
            effective_policy,
            effective_postconditions,
            allow_document_change=allow_document_change,
        )
        if table_result is not None:
            if snapshot is not None:
                self._structure_snapshot = snapshot
            return natural_execution.complete(table_result)
        recipe_result = operate_resolved_recipe(
            candidate,
            hwp,
            self._live_edit_history,
            routing_page,
            resolution,
            target,
            data,
            assets,
            effective_policy,
            effective_postconditions,
            recipe,
            inputs,
            layout,
            resolve_only=resolve_only,
            allow_document_change=allow_document_change,
        )
        if recipe_result is not None:
            return natural_execution.complete(recipe_result)
        if resolution.status == "ambiguous" and not atomic_rescue:
            return workflow_result(
                resolution,
                "ambiguous",
                "공식 API를 추측하지 않았습니다. 실제 작업군 후보 중 하나를 inputs.operation으로 지정하세요",
            )
        if layout is not None:
            raise HwpLiveError(
                "layout 입력은 문서 레이아웃 삽입 레시피 의도와 함께 사용해야 합니다"
            )
        if natural_execution.workflow is not None:
            return natural_execution.unsupported_dispatch_result(resolution)
        result = operate_validated(
            hwp,
            candidate,
            intent_or_operation_id,
            inputs,
            resolve_only=resolve_only,
            allow_document_change=allow_document_change,
            use_defaults=use_defaults,
            expected_cursor=expected_cursor,
            unsafe_selectors=self._unsafe_selectors,
            guard=guard,
        )
        return natural_execution.complete(result)

    def last_operation_routing_context(
        self,
        session_id: str,
    ) -> OperationRoutingContext:
        if self._session_id is None or session_id != self._session_id:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        if self._last_routing_context is None:
            raise HwpLiveError("최근 hwp_operate 빠른 구조 컨텍스트가 없습니다")
        return self._last_routing_context
