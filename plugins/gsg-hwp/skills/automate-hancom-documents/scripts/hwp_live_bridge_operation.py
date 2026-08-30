from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from hwp_errors import HwpLiveError
from hwp_layout_preflight import LayoutPreflightResult
from hwp_live_bridge_mixin import HancomBridgeSessionRuntime
from hwp_live_contract import LayoutPlan
from hwp_pageplan_g04_contract import G04ApplyRequest, G04ApplyResponse
from hwp_live_native_batch import probe_official_api
from hwp_official_api_live import (
    OfficialApiLiveBatchResult,
    OfficialApiLiveCategory,
    run_official_api_live_batch,
)
from hwp_operation_contract import (
    HwpOperateAssets,
    HwpOperateData,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    HwpWorkflowId,
    OperationInputValue,
    OperationResult,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs


STYLE_READ_UNVERIFIED_REASON_CODE: Final = "style_read_unverified"


def _unverified_preflight(result: LayoutPreflightResult) -> LayoutPreflightResult:
    if STYLE_READ_UNVERIFIED_REASON_CODE in result.reason_codes:
        return result
    return result.model_copy(
        update={
            "reason_codes": (
                *result.reason_codes,
                STYLE_READ_UNVERIFIED_REASON_CODE,
            )
        }
    )


class HancomBridgeOperationMixin(HancomBridgeSessionRuntime):
    __slots__: tuple[str, ...] = ()

    def apply_page_plan(
        self,
        session_id: str,
        request: G04ApplyRequest,
    ) -> G04ApplyResponse:
        return self._call_mutation(
            lambda: self._bridge_controller().apply_page_plan(session_id, request),
            session_id=session_id,
        )

    def preflight_layout(
        self,
        session_id: str,
        plan: LayoutPlan,
    ) -> LayoutPreflightResult:
        # 읽기 전용 예측이다. 토큰이 계속 움직였다고 거부하면 레이아웃을 미리
        # 재보는 길 자체가 막히므로, 계산한 값을 주고 reason_codes 로 "이 값이
        # 최신 문서 상태 기준이라고 보증하지 못한다"만 알린다.
        return self._call_style_read(
            lambda controller: controller.preflight_layout(session_id, plan),
            session_id=session_id,
            degrade=_unverified_preflight,
        )

    def run_official_api_batch(
        self,
        session_id: str,
        category: OfficialApiLiveCategory,
        start: int,
        limit: int,
    ) -> OfficialApiLiveBatchResult:
        return self._call_mutation(
            lambda: self._bridge_controller().run_official_api_batch(
                session_id,
                category,
                start,
                limit,
            ),
            session_id=session_id,
        )

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
        def invoke() -> OperationResult:
            controller = self._bridge_controller()
            controller.set_style_state_token(
                session_id,
                self._style_state_token(session_id),
            )
            result = controller.operate(
                session_id,
                intent_or_operation_id,
                inputs,
                resolve_only=resolve_only,
                allow_document_change=allow_document_change,
                use_defaults=use_defaults,
                expected_cursor=expected_cursor,
                workflow=workflow,
                target=target,
                data=data,
                assets=assets,
                policy=policy,
                postconditions=postconditions,
                layout=layout,
                recipe=recipe,
            )
            return result.model_copy(
                update={
                    "routing_context": controller.last_operation_routing_context(
                        session_id
                    )
                }
            )

        return self._call_mutation(
            invoke,
            session_id=session_id,
            save_operation=(
                workflow in {"document.save", "document.save_reopen_verify"}
                and not resolve_only
                and allow_document_change
            ),
        )

    def probe_official_api_batch(
        self,
        window_handle: int,
        category: OfficialApiLiveCategory,
        start: int,
        limit: int,
    ) -> OfficialApiLiveBatchResult:
        if window_handle < 1:
            raise HwpLiveError("한컴 창 핸들은 1 이상이어야 합니다")
        return self._call_mutation(
            lambda: run_official_api_live_batch(
                window_handle,
                category,
                start,
                limit,
            ),
            process_id=self._bridge_process_id(window_handle),
        )

    def probe_official_api_payload(
        self,
        window_handle: int,
        payload: str,
    ) -> str:
        if window_handle < 1:
            raise HwpLiveError("한컴 창 핸들은 1 이상이어야 합니다")
        response = self._call_mutation(
            lambda: probe_official_api(window_handle, payload),
            process_id=self._bridge_process_id(window_handle),
        )
        if response is None:
            raise HwpLiveError("C++/ATL ProbeOfficialApi를 사용할 수 없습니다")
        return response
