from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication
from hwp_live_native_action_models import (
    CaptionCommand,
    CaptureTableCommand,
    InsertPictureCommand,
    IntegerValue,
    MoveDocumentEndCommand,
    MovePositionCommand,
    NativeActionCommand,
    NativeDetailedCaption,
    NativePageInspection,
    NativeSnapshot,
    NativeSetter,
    ParameterActionCommand,
    SelectControlCommand,
    TextValue,
)
from hwp_live_native_batch import inspect_native_structure, read_native_snapshot
from hwp_live_native_template_repeat import (
    caption_literal_text,
    replace_table_caption_commands,
)
from hwp_live_native_text_format import style_command
from hwp_live_rot import HwpDocumentCandidate
from hwp_operation_contract import (
    HwpOperateAssets,
    HwpOperateTarget,
    HwpWorkflowId,
    OperationResult,
    WorkflowResolution,
)
from hwp_priority_object_inputs import (
    ControlTargetRequest,
    native_position,
    resolve_control_target,
    resolve_style_position,
    single_image,
)
from hwp_priority_object_runtime import (
    execute_object_commands as _execute,
    object_recipe_result as _result,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs


_OBJECT_WORKFLOWS = frozenset[HwpWorkflowId](
    ("image.insert", "image.replace", "caption.add", "style.copy", "style.apply")
)


def _selection_snapshot(
    candidate: HwpDocumentCandidate,
    target: HwpOperateTarget | None,
) -> NativeSnapshot | None:
    if target is None or target.binding != "selection":
        return None
    snapshot = read_native_snapshot(candidate.window_handle)
    if snapshot is None:
        raise HwpLiveError("한컴 네이티브 선택 상태를 읽지 못했습니다")
    return snapshot


def operate_object_recipe(
    candidate: HwpDocumentCandidate,
    hwp: LiveHwpApplication,
    routing_page: NativePageInspection,
    resolution: WorkflowResolution,
    target: HwpOperateTarget | None,
    assets: HwpOperateAssets | None,
    recipe_inputs: HwpPriorityRecipeInputs | None,
    *,
    resolve_only: bool,
    allow_document_change: bool,
) -> OperationResult | None:
    _ = hwp
    workflow = resolution.workflow_id
    if workflow not in _OBJECT_WORKFLOWS:
        return None
    if resolve_only:
        return _result(resolution, "resolved", "인증된 프로토콜 9 개체 recipe를 확정했습니다")
    if not allow_document_change:
        return _result(resolution, "confirmation_required", "문서 개체를 변경하는 작업입니다")
    commands: tuple[NativeActionCommand, ...]
    caption_text: str | None = None
    caption_control_id: str | None = None
    caption_before: NativeDetailedCaption | None = None
    resolved_target_id: str | None = None
    target_resolution_basis: str | None = None
    if workflow == "image.insert":
        image = single_image(assets)
        if image is None:
            return _result(
                resolution, "needs_input", "삽입할 그림 한 개가 필요합니다",
                required_inputs=("inputs.assets.images",),
            )
        target_position = (
            None if recipe_inputs is None else recipe_inputs.target_position
        )
        document_end = (
            target is not None
            and target.kind == "document"
            and target.scope == "document"
        )
        prefix: tuple[NativeActionCommand, ...] = (
            (MovePositionCommand(native_position(target_position)),)
            if target_position is not None
            else ((MoveDocumentEndCommand(),) if document_end else ())
        )
        if target_position is not None:
            resolved_target_id = (
                f"position:{target_position.list_id}:"
                f"{target_position.paragraph}:"
                f"{target_position.character}"
            )
            target_resolution_basis = "explicit_recipe_target_position"
        if document_end and target_position is None:
            resolved_target_id = "document:end"
            target_resolution_basis = "target.document_end"
        width = None if recipe_inputs is None else recipe_inputs.picture_width_mm
        height = None if recipe_inputs is None else recipe_inputs.picture_height_mm
        commands = (*prefix, InsertPictureCommand(image, width, height))
    elif workflow == "image.replace":
        image = single_image(assets)
        resolved_control = resolve_control_target(
            ControlTargetRequest(
                routing_page,
                target,
                frozenset(("gso", "pic", "picture")),
                _selection_snapshot(candidate, target),
            )
        )
        control_id = None if resolved_control is None else resolved_control.instance_id
        target_resolution_basis = (
            None if resolved_control is None else resolved_control.basis
        )
        if image is None or control_id is None:
            return _result(
                resolution, "needs_input", "교체할 그림 한 개와 고유한 그림 대상이 필요합니다",
                required_inputs=("inputs.target.control_instance_id", "inputs.assets.images"),
            )
        resolved_target_id = control_id
        commands = (
            SelectControlCommand(control_id),
            ParameterActionCommand(
                "PictureChange",
                "HPictureChange",
                (
                    NativeSetter("PicturePath", TextValue(str(image))),
                    NativeSetter(
                        "PictureEmbed",
                        IntegerValue(
                            1
                            if recipe_inputs is None or recipe_inputs.picture_embed
                            else 0
                        ),
                    ),
                ),
            ),
        )
    elif workflow == "caption.add":
        resolved_control = resolve_control_target(
            ControlTargetRequest(
                routing_page,
                target,
                frozenset(("tbl", "gso", "pic", "picture")),
                _selection_snapshot(candidate, target),
            )
        )
        control_id = None if resolved_control is None else resolved_control.instance_id
        target_resolution_basis = (
            None if resolved_control is None else resolved_control.basis
        )
        if recipe_inputs is None or recipe_inputs.caption_text is None or control_id is None:
            return _result(
                resolution, "needs_input", "캡션 텍스트와 고유한 표 대상이 필요합니다",
                required_inputs=("inputs.target.control_instance_id", "inputs.recipe.caption_text"),
            )
        caption_text = recipe_inputs.caption_text.strip()
        if not caption_text:
            return _result(
                resolution, "needs_input", "캡션 텍스트가 비어 있습니다",
                required_inputs=("inputs.recipe.caption_text",),
            )
        before_structure = inspect_native_structure(
            candidate.window_handle,
            routing_page.page,
        )
        if before_structure is None:
            raise HwpLiveError("네이티브 캡션 변경 전 상세 구조를 읽지 못했습니다")
        existing = tuple(
            caption
            for caption in before_structure.captions
            if caption.table_instance_id == control_id
        )
        if len(existing) > 1:
            raise HwpLiveError("대상 표의 기존 캡션이 하나보다 많습니다")
        caption_before = existing[0] if existing else None
        if caption_before is None:
            commands = (
                SelectControlCommand(control_id),
                CaptureTableCommand(),
                CaptionCommand(recipe_inputs.caption_style_id, caption_text),
            )
        else:
            commands = replace_table_caption_commands(
                control_id,
                caption_literal_text(
                    caption_before.text,
                    automatic_number=caption_before.automatic_number,
                ),
                caption_text,
            )
        caption_control_id = control_id
        resolved_target_id = control_id
    elif workflow == "style.apply":
        style_target = resolve_style_position(
            recipe_inputs,
            target,
            _selection_snapshot(candidate, target),
        )
        if recipe_inputs is None or style_target is None or recipe_inputs.style_id is None:
            return _result(
                resolution, "needs_input", "스타일 ID와 적용 위치가 필요합니다",
                required_inputs=("inputs.recipe.style_id", "inputs.target"),
            )
        commands = (
            MovePositionCommand(native_position(style_target.position)),
            style_command(recipe_inputs.style_id),
        )
        resolved_target_id = (
            f"position:{style_target.position.list_id}:"
            f"{style_target.position.paragraph}:"
            f"{style_target.position.character}"
        )
        target_resolution_basis = style_target.basis
    else:
        if recipe_inputs is None or recipe_inputs.source_position is None or recipe_inputs.target_position is None:
            return _result(
                resolution, "needs_input", "원본과 대상 텍스트 위치가 필요합니다",
                required_inputs=("inputs.recipe.source_position", "inputs.recipe.target_position"),
            )
        transfer = ParameterActionCommand(
            "ShapeCopyPaste",
            "HShapeCopyPaste",
            (NativeSetter("Type", IntegerValue(recipe_inputs.style_copy_type)),),
        )
        commands = (
            MovePositionCommand(native_position(recipe_inputs.source_position)),
            transfer,
            MovePositionCommand(native_position(recipe_inputs.target_position)),
            transfer,
        )
        resolved_target_id = (
            f"position:{recipe_inputs.target_position.list_id}:"
            f"{recipe_inputs.target_position.paragraph}:"
            f"{recipe_inputs.target_position.character}"
        )
        target_resolution_basis = "explicit_recipe_target_position"
    executed, elapsed, current_page, page_count, modified = _execute(candidate, commands)
    if workflow == "caption.add":
        if caption_text is None or caption_control_id is None:
            raise HwpLiveError("caption recipe executed without caption verification inputs")
        after = inspect_native_structure(
            candidate.window_handle,
            routing_page.page,
        )
        if after is None:
            raise HwpLiveError("네이티브 캡션 변경 후 상세 구조를 읽지 못했습니다")
        captions = tuple(
            caption
            for caption in after.captions
            if caption.table_instance_id == caption_control_id
        )
        if len(captions) != 1:
            raise HwpLiveError("네이티브 캡션 결과를 상세 구조에서 하나로 확인하지 못했습니다")
        caption_after = captions[0]
        actual_title = caption_literal_text(
            caption_after.text,
            automatic_number=caption_after.automatic_number,
        )
        if actual_title != caption_text:
            raise HwpLiveError(
                f"네이티브 캡션 제목이 요청과 다릅니다: {actual_title!r}"
            )
        if caption_before is not None and (
            caption_after.automatic_number,
            caption_after.style_id,
            caption_after.style_name,
        ) != (
            caption_before.automatic_number,
            caption_before.style_id,
            caption_before.style_name,
        ):
            raise HwpLiveError("기존 캡션의 자동번호 또는 스타일이 변경되었습니다")
    return _result(resolution, "executed", "프로토콜 9 C++/ATL 네이티브 개체 recipe를 실행했습니다").model_copy(
        update={
            "execution_mode": "native_in_process",
            "native_protocol": 9,
            "verification": (
                "native_detailed_structure_before_after"
                if workflow == "caption.add"
                else "native_snapshot_before_after"
            ),
            "commands_executed": executed,
            "native_elapsed_microseconds": elapsed,
            "current_page": current_page,
            "page_count": page_count,
            "modified": modified,
            "resolved_target_id": resolved_target_id,
            "target_resolution_basis": target_resolution_basis,
        }
    )
