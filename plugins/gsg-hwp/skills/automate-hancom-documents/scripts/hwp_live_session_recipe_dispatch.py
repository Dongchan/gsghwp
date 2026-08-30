from __future__ import annotations

from collections.abc import Mapping

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_contract import LayoutPlan
from hwp_live_edit_history import LiveEditHistoryStore
from hwp_live_native_action_models import NativePageInspection
from hwp_live_native_format_recipe import (
    NativeFormatRecipeRequest,
    operate_native_format_recipe,
)
from hwp_live_document_edit_recipe import (
    NativeDocumentEditRequest,
    operate_native_document_edit,
)
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_session_workflow import WORKFLOW_REQUIRED_INPUTS, workflow_result
from hwp_operation_certification import certified_recipe
from hwp_operation_contract import (
    HwpOperateAssets,
    HwpOperateData,
    HwpOperatePolicy,
    HwpOperatePostconditions,
    HwpOperateTarget,
    OperationInputValue,
    OperationResult,
    WorkflowResolution,
)
from hwp_priority_object_recipes import operate_object_recipe
from hwp_priority_picture_edit import PictureEditRequest, operate_picture_edit_recipe
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs
from hwp_priority_table_recipes import operate_table_recipe


def operate_resolved_recipe(
    candidate: HwpDocumentCandidate,
    hwp: LiveHwpApplication,
    live_edit_history: LiveEditHistoryStore,
    routing_page: NativePageInspection,
    resolution: WorkflowResolution,
    target: HwpOperateTarget | None,
    data: HwpOperateData | None,
    assets: HwpOperateAssets | None,
    policy: HwpOperatePolicy,
    postconditions: HwpOperatePostconditions,
    recipe: HwpPriorityRecipeInputs | None,
    inputs: Mapping[str, OperationInputValue],
    layout: LayoutPlan | None,
    *,
    resolve_only: bool,
    allow_document_change: bool,
) -> OperationResult | None:
    if resolution.status != "resolved":
        return None
    candidate_workflow = resolution.candidates[0]
    document_edit = operate_native_document_edit(
        NativeDocumentEditRequest(
            candidate=candidate,
            history=live_edit_history,
            routing_page=routing_page,
            resolution=resolution,
            target=target,
            parameters=inputs,
            resolve_only=resolve_only,
            allow_document_change=allow_document_change,
        )
    )
    if document_edit is not None:
        return document_edit
    native_format = operate_native_format_recipe(
        NativeFormatRecipeRequest(
            candidate=candidate,
            routing_page=routing_page,
            resolution=resolution,
            target=target,
            parameters=inputs,
            postconditions=postconditions,
            resolve_only=resolve_only,
            allow_document_change=allow_document_change,
        )
    )
    if native_format is not None:
        return native_format

    priority_table = operate_table_recipe(
        candidate,
        hwp,
        resolution,
        target,
        data,
        assets,
        policy,
        postconditions,
        recipe,
        resolve_only=resolve_only,
        allow_document_change=allow_document_change,
        history=live_edit_history,
    )
    if priority_table is not None:
        return priority_table
    picture_edit = operate_picture_edit_recipe(
        PictureEditRequest(
            candidate=candidate,
            hwp=hwp,
            routing_page=routing_page,
            resolution=resolution,
            target=target,
            parameters=inputs,
            resolve_only=resolve_only,
            allow_document_change=allow_document_change,
            recipe=recipe,
        )
    )
    if picture_edit is not None:
        return picture_edit
    priority_object = operate_object_recipe(
        candidate,
        hwp,
        routing_page,
        resolution,
        target,
        assets,
        recipe,
        resolve_only=resolve_only,
        allow_document_change=allow_document_change,
    )
    if priority_object is not None:
        return priority_object
    if candidate_workflow.execution == "official":
        return None
    if certified_recipe(candidate_workflow.workflow_id) is None:
        return workflow_result(
            resolution,
            "unsupported",
            "작업군은 찾았지만 production에서 인증된 recipe graph가 없습니다",
        )
    if layout is not None:
        raise HwpLiveError("layout 입력은 문서 끝 레이아웃 작업과 함께 사용해야 합니다")
    if resolve_only:
        return workflow_result(
            resolution,
            "resolved",
            "고수준 한컴 작업군과 네이티브 레시피를 확정했습니다",
        )
    required_inputs = WORKFLOW_REQUIRED_INPUTS.get(
        candidate_workflow.workflow_id,
        ("inputs.target",),
    )
    return workflow_result(
        resolution,
        "needs_input",
        "작업군은 확정했습니다. 반환된 구조화 입력 필드를 전달하세요",
        required_inputs=required_inputs,
    )
