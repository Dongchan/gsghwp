from __future__ import annotations

from collections.abc import Mapping

from hwp_errors import HwpLiveError
from hwp_live_bridge_mixin import HancomBridgeMutationRuntime
from hwp_live_contract import LayoutPlan
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


class HancomBridgeOperationMixin(HancomBridgeMutationRuntime):
    __slots__: tuple[str, ...] = ()

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
            )
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

        return self._call_mutation(invoke)

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
            )
        )

    def probe_official_api_payload(
        self,
        window_handle: int,
        payload: str,
    ) -> str:
        if window_handle < 1:
            raise HwpLiveError("한컴 창 핸들은 1 이상이어야 합니다")
        response = self._call_mutation(
            lambda: probe_official_api(window_handle, payload)
        )
        if response is None:
            raise HwpLiveError("C++/ATL ProbeOfficialApi를 사용할 수 없습니다")
        return response
