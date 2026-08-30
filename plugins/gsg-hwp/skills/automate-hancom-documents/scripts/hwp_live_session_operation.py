from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from hwp_errors import HwpLiveError
from hwp_layout_preflight import LayoutPreflightResult, preflight_layout
from hwp_live_contract import LayoutPlan
from hwp_live_edit_history_runtime import (
    LayoutEditRecovery,
    execute_managed_layout_edit,
)
from hwp_live_auto_route import (
    prepare_natural_route,
    resolve_conservative_route,
)
from hwp_live_native_action_results import NativeSnapshot
from hwp_live_native_batch import (
    execute_native_lifecycle,
    execute_native_save,
    forget_cached_content_signatures,
    read_native_content_signature,
    read_native_snapshot,
)
from hwp_live_operation import operate_validated
from hwp_live_operation_recipe import (
    is_document_end_layout_intent,
    native_style_plan,
    operate_layout,
)
from hwp_live_safety import require_writable_document
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_session_data import LiveHwpDataSession
from hwp_live_session_lifecycle import (
    lifecycle_preflight_result,
    lifecycle_result,
    save_preflight_result,
    save_result,
)
from hwp_live_session_recipe_dispatch import operate_resolved_recipe
from hwp_live_session_routing import read_operation_routing_context
from hwp_live_session_table_fill import operate_table_fill
from hwp_live_session_workflow import (
    WorkflowPreflightInputs,
    explicit_workflow_preflight,
    workflow_policy,
    workflow_result,
)
from hwp_live_grounding import (
    build_convention_evidence,
    build_page_geometry,
    content_signature_sha256,
)
from hwp_pageplan_assets import verify_source_registry
from hwp_pageplan_contract import (
    canonical_page_plan_sha256,
    verify_page_plan_sources,
)
from hwp_pageplan_g04 import (
    G04PreparationError,
    g04_error_from_exception,
    g04_overflow_hint,
    inspect_hwpml_images,
    png_dimensions,
    prepare_g04_application,
    sha256_file,
    source_evidence_for_plan,
    verify_embedded_images,
)
from hwp_pageplan_g04_contract import (
    G04_ATOMIC_BOUNDARY,
    G04ApplyRequest,
    G04ApplyResponse,
    G04EmbeddedAssetEvidence,
    G04Error,
    G04ErrorCode,
    G04RenderEvidence,
    G04RollbackEvidence,
    G04UndoEvidence,
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

    def preflight_layout(
        self,
        session_id: str,
        plan: LayoutPlan,
    ) -> LayoutPreflightResult:
        candidate, hwp = self._validate(session_id)
        guard = self._guard(candidate, hwp)
        snapshot = read_native_snapshot(candidate.window_handle)
        if snapshot is None:
            raise HwpLiveError(
                "레이아웃 사전 검사 전에 한컴 문서 상태를 읽지 못했습니다"
            )
        if (
            plan.target == "after_page"
            and plan.page is not None
            and plan.page > snapshot.page_count
        ):
            raise HwpLiveError("레이아웃 삽입 기준 쪽이 현재 문서 범위를 벗어났습니다")
        setup_page = (
            plan.page
            if plan.target == "after_page" and plan.page is not None
            else snapshot.page_count
            if plan.target == "document_end"
            else snapshot.current_page
        )
        layout_page = setup_page + 1 if plan.target == "after_page" else setup_page
        resolved, geometry, _, _ = native_style_plan(
            candidate,
            plan,
            snapshot.style_id,
            guard,
            setup_page=setup_page,
            style_list=self.styles(
                session_id,
                allow_historical_convention=False,
            ),
        )
        return preflight_layout(
            resolved,
            geometry,
            page_number=layout_page,
        )

    def apply_page_plan(
        self,
        session_id: str,
        request: G04ApplyRequest,
    ) -> G04ApplyResponse:
        """Apply one accepted G03 page as one append transaction.

        This is intentionally separate from the general LayoutPlan operation:
        G04 has stronger invariants than the legacy layout tools, including a
        one-page boundary, raw-image proof, and one final render. Undo evidence
        is reported, not required: the write is the work and the MCP undo entry
        is bookkeeping, so a missing entry is said out loud rather than turned
        into a failure.
        """
        plan = request.compiled.plan
        plan_sha256 = canonical_page_plan_sha256(plan)
        candidate_name = plan.candidate

        def rejected(error: G04Error) -> G04ApplyResponse:
            return G04ApplyResponse(
                status="rejected",
                operation_id=request.operation_id,
                candidate=candidate_name,
                plan_sha256=plan_sha256,
                source_manifest_sha256=request.compiled.source_manifest_sha256,
                error=error,
            )

        try:
            lowered = prepare_g04_application(
                request.compiled,
                request.source_registry,
                request.document_selector,
            )
        except G04PreparationError as error:
            return rejected(g04_error_from_exception(error))

        candidate, hwp = self._validate(session_id)
        guard = self._guard(candidate, hwp)
        try:
            before = read_native_snapshot(candidate.window_handle)
            # 이 관문은 언제나 엔진을 본다. 캐시를 먼저 버리는 것이 그 약속을
            # 코드에 못박는 자리다.
            #
            # 아래 대조가 잡으라고 있는 것은 "G01 이 관측한 뒤 문서가 바뀌었다"
            # 이고, 그 변경의 대표적인 출처는 이 워커가 모르는 것들이다 —
            # 사용자가 한/글에서 직접 친 글, 브리지 밖에서 도는 다른 워커.
            # 그런데 캐시를 채우고 버리지 않는 유일한 읽기 경로가 바로 그
            # G01(ground_document)이다. 캐시에 물어보면 G01 이 남긴 자기 값을
            # 되받아 무조건 일치하고, 관문은 조용히 사라진다.
            #
            # `_call_mutation` 의 진입 무효화가 지금은 같은 일을 해 주지만,
            # 관문의 정확성이 호출자 쪽 한 줄에 매달려 있어서는 안 된다.
            forget_cached_content_signatures(candidate.window_handle)
            before_content_signature = read_native_content_signature(
                candidate.window_handle
            )
            if before is None or before_content_signature is None:
                return rejected(
                    G04Error(
                        code="STALE_GROUNDING",
                        message="G04 실행 전 문서 상태 또는 본문 지문을 읽지 못했습니다",
                    )
                )
            revision_hash = content_signature_sha256(before_content_signature)
            if revision_hash != plan.g01_observation.document_revision_hash:
                return rejected(
                    G04Error(
                        code="STALE_GROUNDING",
                        message="G01 문서 revision과 현재 문서 본문 지문이 다릅니다",
                        expected=plan.g01_observation.document_revision_hash,
                        actual=revision_hash,
                    )
                )
            setup = self._grounding_page_setup(
                session_id,
                1,
                before.current_page,
            )
            expected_setup_model = plan.g01_observation.page_setup
            expected_setup = expected_setup_model.model_dump(mode="json")
            actual_setup = setup.model_dump(mode="json")
            setup_numeric_pairs = (
                (setup.paper_width_mm, expected_setup_model.paper_width_mm),
                (setup.paper_height_mm, expected_setup_model.paper_height_mm),
                (setup.top_margin_mm, expected_setup_model.top_margin_mm),
                (setup.bottom_margin_mm, expected_setup_model.bottom_margin_mm),
                (setup.left_margin_mm, expected_setup_model.left_margin_mm),
                (setup.right_margin_mm, expected_setup_model.right_margin_mm),
                (setup.header_mm, expected_setup_model.header_mm),
                (setup.footer_mm, expected_setup_model.footer_mm),
                (setup.gutter_mm, expected_setup_model.gutter_mm),
            )
            setup_matches = (
                all(
                    abs(actual - expected) <= 0.01
                    for actual, expected in setup_numeric_pairs
                )
                and setup.landscape == expected_setup_model.landscape
                and setup.gutter_type == expected_setup_model.gutter_type
            )
            if not setup_matches:
                return rejected(
                    G04Error(
                        code="PAGE_SETUP_MISMATCH",
                        message="G01 쪽 설정과 현재 문서 쪽 설정이 다릅니다",
                        expected=expected_setup,
                        actual=actual_setup,
                    )
                )
            live_geometry = build_page_geometry(setup, 1)
            expected_body = plan.g01_observation.body_box
            actual_body = live_geometry.body_box_mm
            actual_body_payload = actual_body.model_dump(mode="json")
            expected_body_payload = expected_body.model_dump(mode="json")
            body_pairs = (
                (actual_body.left_mm, expected_body.left_mm),
                (actual_body.top_mm, expected_body.top_mm),
                (actual_body.width_mm, expected_body.width_mm),
                (actual_body.height_mm, expected_body.height_mm),
            )
            body_matches = all(
                abs(actual - expected) <= 0.02 for actual, expected in body_pairs
            )
            if not body_matches:
                return rejected(
                    G04Error(
                        code="BODY_GEOMETRY_MISMATCH",
                        message="G01 본문 상자와 현재 문서 본문 상자가 다릅니다",
                        expected=expected_body_payload,
                        actual=actual_body_payload,
                    )
                )
            structure = self.structure(session_id, 1)
            style_list = self.styles(
                session_id,
                allow_historical_convention=False,
            )
            self.finish_public_convention_read()
            conventions = build_convention_evidence(style_list, (structure,))
            if (
                conventions.convention_profile_hash
                != plan.g01_observation.convention_profile_hash
            ):
                return rejected(
                    G04Error(
                        code="CONVENTION_MISMATCH",
                        message="G01 관례 프로필과 현재 문서 관례 프로필이 다릅니다",
                        expected=plan.g01_observation.convention_profile_hash,
                        actual=conventions.convention_profile_hash,
                    )
                )
            source_evidence = source_evidence_for_plan(
                plan,
                request.source_registry,
            )
            before_hwpml = ""
            if lowered.images:
                guard()
                before_hwpml = hwp.get_text_file("HWPML2X", "")
                guard()
                if not before_hwpml:
                    return rejected(
                        G04Error(
                            code="EMBEDDED_BYTES_MISMATCH",
                            message="그림 삽입 전 HWPML2X 원본을 읽지 못했습니다",
                        )
                    )
        except HwpLiveError as error:
            return rejected(
                G04Error(
                    code="DOCUMENT_UNAVAILABLE",
                    message=str(error),
                )
            )

        execution: dict[str, object] = {}
        embedded_evidence: tuple[G04EmbeddedAssetEvidence, ...] = ()
        render_evidence: G04RenderEvidence | None = None
        render_count = 0
        after_snapshot_evidence: NativeSnapshot | None = None
        after_signature_evidence: str | None = None

        def capture_hwpml() -> str:
            guard()
            content = hwp.get_text_file("HWPML2X", "")
            guard()
            if not content:
                raise G04PreparationError(
                    "EMBEDDED_BYTES_MISMATCH",
                    "G04 실행 후 HWPML2X 원본을 읽지 못했습니다",
                )
            return content

        def execute_layout() -> OperationResult:
            nonlocal embedded_evidence, render_evidence
            nonlocal render_count
            nonlocal after_snapshot_evidence, after_signature_evidence
            result = operate_layout(
                candidate,
                "document.append_layout",
                lowered.layout,
                resolve_only=False,
                allow_document_change=True,
                expected_cursor=None,
                unsafe_selectors=self._unsafe_selectors,
                guard=guard,
                atomic=True,
                style_list=style_list,
            )
            if result.status != "executed":
                if result.changed:
                    raise G04PreparationError(
                        "ATOMIC_EXECUTION_FAILED",
                        "원자 레이아웃 실행이 완전한 성공 상태를 반환하지 않았습니다",
                    )
                execution["not_executed"] = result
                return result

            after = read_native_snapshot(candidate.window_handle)
            if after is None:
                raise G04PreparationError(
                    "ATOMIC_EXECUTION_FAILED",
                    "원자 레이아웃 실행 후 문서 상태를 읽지 못했습니다",
                )
            if after.page_count != before.page_count + 1:
                # "한 쪽으로 끝나지 않았다"만으로는 호출자가 계획의 어디를 얼마나
                # 줄여야 하는지 알 수 없다. 계획이 예측한 바닥과 본문 바닥의 차이를
                # 실어 보내면 그대로 계획을 고칠 수 있다.
                raise G04PreparationError(
                    "LAYOUT_OVERFLOW",
                    "G04 one-page layout이 정확히 한 쪽으로 끝나지 않았습니다"
                    + g04_overflow_hint(
                        lowered,
                        expected_body,
                        expected_pages=before.page_count + 1,
                        actual_pages=after.page_count,
                    ),
                    expected=before.page_count + 1,
                    actual=after.page_count,
                )
            after_content_signature = read_native_content_signature(
                candidate.window_handle
            )
            if after_content_signature is None:
                raise G04PreparationError(
                    "ATOMIC_EXECUTION_FAILED",
                    "원자 레이아웃 실행 후 본문 지문을 읽지 못했습니다",
                )
            embedded: tuple[G04EmbeddedAssetEvidence, ...] = ()
            if lowered.images:
                proof = verify_embedded_images(
                    before_hwpml,
                    capture_hwpml(),
                    lowered.images,
                )
                embedded = proof.evidence
                embedded_evidence = embedded
                execution["embedded"] = embedded
                if not proof.complete:
                    failed = next(
                        item
                        for item in embedded
                        if not item.byte_fidelity or not item.aspect_preserved
                    )
                    code = (
                        "EMBEDDED_BYTES_MISMATCH"
                        if not failed.byte_fidelity
                        else "ASPECT_RATIO_MISMATCH"
                    )
                    raise G04PreparationError(
                        code,
                        f"삽입된 원본 그림 검증에 실패했습니다: {failed.source_ref}",
                        source_ref=failed.source_ref,
                        expected=failed.expected_sha256,
                        actual=failed.embedded_sha256s,
                    )
            final_page = before.page_count + 1
            try:
                render_count += 1
                rendered = self.render_page(
                    session_id,
                    final_page,
                    request.render_dpi,
                )
                width_px, height_px = png_dimensions(rendered.path)
                if render_count != 1:
                    raise G04PreparationError(
                        "RENDER_FAILED",
                        "G04 최종 렌더 호출 횟수가 정확히 한 번이 아닙니다",
                    )
                render_evidence = G04RenderEvidence(
                    path=rendered.path,
                    sha256=sha256_file(rendered.path),
                    page=final_page,
                    dpi=request.render_dpi,
                    width_px=width_px,
                    height_px=height_px,
                    render_count=1,
                )
            except (HwpLiveError, OSError, ValueError) as error:
                raise G04PreparationError(
                    "RENDER_FAILED",
                    f"G04 최종 렌더에 실패했습니다: {error}",
                ) from error
            after_snapshot_evidence = after
            after_signature_evidence = after_content_signature
            execution["render"] = render_evidence
            execution["after"] = after
            execution["after_content_signature"] = after_content_signature
            return result

        # This is the authoritative final source decision. It must precede
        # execute_managed_layout_edit because that function captures the
        # pre-edit document checkpoint before it invokes the write closure.
        try:
            verify_page_plan_sources(
                plan,
                request.source_registry,
                require_final_assets=True,
            )
            verify_source_registry(
                request.source_registry,
                require_final_assets=True,
            )
        except ValueError as error:
            message = str(error)
            code = (
                "NON_FINAL_INSERTABLE_ASSET"
                if "final-insertable" in message or "final_insertable" in message
                else "SOURCE_HASH_MISMATCH"
            )
            return rejected(G04Error(code=cast(G04ErrorCode, code), message=message))

        # 무엇을 되돌릴 수 있었는지는 예외로 나가는 길에도 알아야 한다. 이
        # 기록이 없던 동안 이 응답은 복원이 한 번도 돌지 않은 실패에도
        # `rolled_back` + `attempted: true` 를 붙였다.
        recovery = LayoutEditRecovery()
        try:
            managed = execute_managed_layout_edit(
                candidate,
                self._live_edit_history,
                "document.append_layout",
                execute_layout,
                require_undo_entry=True,
                restore_on_exception=True,
                recovery=recovery,
            )
        except Exception as error:
            rollback = self._g04_rollback_evidence(
                session_id,
                candidate,
                before,
                before_content_signature,
                before_hwpml,
                bool(lowered.images),
                attempted=recovery.restore_attempted,
            )
            runtime_code = getattr(error, "code", None)
            if runtime_code == "G04PreparationError":
                runtime_code = None
            error_code = (
                runtime_code
                if runtime_code
                in {
                    "EMBEDDED_BYTES_MISMATCH",
                    "ASPECT_RATIO_MISMATCH",
                    "LAYOUT_OVERFLOW",
                    "SOURCE_HASH_MISMATCH",
                    "NON_FINAL_INSERTABLE_ASSET",
                    "RENDER_FAILED",
                    "ATOMIC_EXECUTION_FAILED",
                }
                else "NATIVE_ERROR"
            )
            message = str(error)
            if not rollback.complete:
                # 되돌리기가 돌고 실패한 것과, 되돌릴 사본이 없어 시도조차 못 한
                # 것은 사용자가 할 일이 다르다. 전자는 복구 사본을 찾아야 하고,
                # 후자는 한/글 이력(Ctrl+Z)이 유일한 길이다. 두 경우에 같은
                # `ROLLBACK_FAILED` 을 주면 그 차이가 사라진다.
                if recovery.restore_attempted:
                    error_code = "ROLLBACK_FAILED"
                else:
                    error_code = "CHECKPOINT_REQUIRED"
                    message = (
                        f"{message}. 편집 전 문서 체크포인트가 없어 되돌리기를 "
                        + "시도하지 못했습니다"
                        + (
                            f" ({recovery.unavailable_reason})"
                            if recovery.unavailable_reason
                            else ""
                        )
                    )
            message = message[:2_000] or "G04 원자 실행이 실패했습니다"
            try:
                error_model = G04Error(
                    code=cast(G04ErrorCode, error_code),
                    message=message,
                )
            except ValueError:
                error_model = G04Error(
                    code="NATIVE_ERROR",
                    message=message,
                )
            rollback_after = read_native_snapshot(candidate.window_handle)
            return G04ApplyResponse(
                status="rolled_back",
                operation_id=request.operation_id,
                candidate=candidate_name,
                plan_sha256=lowered.plan_sha256,
                source_manifest_sha256=request.compiled.source_manifest_sha256,
                changed=not rollback.complete,
                native_batch_count=1,
                page_count_before=before.page_count,
                page_count_after=(
                    None if rollback_after is None else rollback_after.page_count
                ),
                before_content_signature=before_content_signature,
                after_content_signature=read_native_content_signature(
                    candidate.window_handle
                ),
                source_evidence=source_evidence,
                rollback=rollback,
                error=error_model,
            )

        if managed.status != "executed" and not isinstance(
            execution.get("not_executed"), OperationResult
        ):
            rollback = self._g04_rollback_evidence(
                session_id,
                candidate,
                before,
                before_content_signature,
                before_hwpml,
                bool(lowered.images),
                attempted=recovery.restore_attempted,
            )
            code = (
                "CHECKPOINT_REQUIRED"
                if "체크포인트" in managed.message or "되돌리기" in managed.message
                else "NATIVE_ERROR"
            )
            rollback_after = read_native_snapshot(candidate.window_handle)
            return G04ApplyResponse(
                status="rolled_back",
                operation_id=request.operation_id,
                candidate=candidate_name,
                plan_sha256=lowered.plan_sha256,
                source_manifest_sha256=request.compiled.source_manifest_sha256,
                changed=not rollback.complete,
                native_batch_count=1,
                commands_executed=managed.commands_executed or 0,
                page_count_before=before.page_count,
                page_count_after=(
                    None if rollback_after is None else rollback_after.page_count
                ),
                before_content_signature=before_content_signature,
                after_content_signature=read_native_content_signature(
                    candidate.window_handle
                ),
                source_evidence=source_evidence,
                rollback=rollback,
                error=G04Error(
                    code=cast(G04ErrorCode, code),
                    message=managed.message,
                ),
            )

        not_executed = execution.get("not_executed")
        if isinstance(not_executed, OperationResult):
            message = not_executed.message
            code = (
                "CHECKPOINT_REQUIRED"
                if "체크포인트" in message or "되돌리기" in message
                else "LAYOUT_OVERFLOW"
                if "넘침" in message or "overflow" in message.casefold()
                else "NATIVE_ERROR"
            )
            return rejected(
                G04Error(
                    code=cast(G04ErrorCode, code),
                    message=message,
                )
            )

        render = render_evidence
        if render is None:
            return rejected(
                G04Error(
                    code="RENDER_FAILED",
                    message="G04 성공 경로가 최종 렌더 증거를 만들지 못했습니다",
                )
            )
        after = after_snapshot_evidence
        after_signature = after_signature_evidence
        if after_signature is None or after is None:
            return rejected(
                G04Error(
                    code="ATOMIC_EXECUTION_FAILED",
                    message="G04 성공 경로가 최종 문서 지문을 만들지 못했습니다",
                )
            )
        # 되돌리기 기록은 부수 장부고, 사용자가 시킨 편집이 일이다. 여기까지
        # 왔다는 것은 네이티브 쓰기·쪽 수·그림 바이트·최종 렌더가 전부 통과했다는
        # 뜻이므로, 장부가 비었다는 이유로 "되돌렸다"고 말하면 거짓말이 된다 —
        # 이 경로는 아무것도 되돌리지 않는다.
        #
        # b733c58 이 `execute_managed_layout_edit` 에서 선불 체크포인트를 걷어낸
        # 뒤로 `document_checkpoint_capture` 를 채울 수 있는 생산 경로는 없다.
        # 그래서 이 값은 언제나 None 이고, 예전 관문은 성공을 한 번도 반환하지
        # 못했다. 없어진 증거원을 요구하는 관문 쪽을 없앤다. 잃은 것이 무엇인지는
        # 아래 세 필드가 그대로 말한다 — MCP 되돌리기 기록은 없고, 되돌리려면
        # 한/글에서 되돌리기(Ctrl+Z)를 쓴다.
        capture_method = managed.document_checkpoint_capture
        undo = G04UndoEvidence(
            entry_created=capture_method is not None,
            one_step_supported=capture_method is not None,
            capture_method=capture_method,
            document_identity_restored=managed.document_identity_restored,
        )
        return G04ApplyResponse(
            status="applied",
            operation_id=request.operation_id,
            candidate=candidate_name,
            plan_sha256=lowered.plan_sha256,
            source_manifest_sha256=request.compiled.source_manifest_sha256,
            changed=True,
            atomic_boundary=G04_ATOMIC_BOUNDARY,
            native_batch_count=1,
            commands_executed=managed.commands_executed or 0,
            page_count_before=before.page_count,
            page_count_after=after.page_count,
            before_content_signature=before_content_signature,
            after_content_signature=after_signature,
            source_evidence=source_evidence,
            embedded_assets=embedded_evidence,
            render=render,
            undo=undo,
        )

    def _g04_rollback_evidence(
        self,
        session_id: str,
        candidate: HwpDocumentCandidate,
        before: NativeSnapshot,
        before_content_signature: str,
        before_hwpml: str,
        had_images: bool,
        *,
        attempted: bool,
    ) -> G04RollbackEvidence:
        """편집 전 상태로 돌아왔는지를 관측하고, 되돌리기를 걸긴 했는지를 싣는다.

        이 함수는 아무것도 되돌리지 않는다. 문서를 읽어 `before` 와 대조할
        뿐이고, `complete` 이하는 전부 그 관측이다. `attempted` 만은 관측이
        아니라 호출자가 아는 사실이어야 한다 — 되돌리기가 실제로 돌았는지는
        문서를 봐서는 알 수 없기 때문이다. 편집 전 체크포인트를 뜨지 못해
        복원 경로가 아예 돌지 않은 실패와, 복원이 돌고도 못 되돌린 실패는
        문서 상태가 같아 보일 수 있고, 사용자가 할 일은 서로 다르다.

        예전에는 `attempted=True` 가 상수였다. 그래서 체크포인트가 없어
        되돌리기를 시도조차 못 한 호출도 "되돌리기를 시도했고 실패했다"고
        답했다.
        """
        try:
            window_handle = candidate.window_handle
            after = read_native_snapshot(window_handle)
            after_content_signature = read_native_content_signature(window_handle)
            content_restored = (
                after_content_signature is not None
                and after_content_signature == before_content_signature
            )
            page_count_restored = (
                after is not None and after.page_count == before.page_count
            )
            structure_restored = content_restored and page_count_restored
            embedded_restored: bool | None = True
            if had_images:
                rollback_hwpml = self._validate(session_id)[1].get_text_file(
                    "HWPML2X", ""
                )
                before_images = inspect_hwpml_images(before_hwpml)
                after_images = inspect_hwpml_images(rollback_hwpml)
                embedded_restored = sorted(
                    item.sha256 for item in before_images.binaries
                ) == sorted(item.sha256 for item in after_images.binaries)
            complete = bool(
                content_restored
                and structure_restored
                and page_count_restored
                and embedded_restored
            )
            return G04RollbackEvidence(
                attempted=attempted,
                complete=complete,
                content_restored=content_restored,
                structure_restored=structure_restored,
                page_count_restored=page_count_restored,
                embedded_bytes_restored=embedded_restored,
            )
        except Exception:
            return G04RollbackEvidence(
                attempted=attempted,
                complete=False,
                content_restored=False,
                structure_restored=False,
                page_count_restored=False,
                embedded_bytes_restored=False if had_images else None,
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
        candidate, hwp = self._validate(session_id)
        self._last_routing_context = None
        routing_page, routing_context = read_operation_routing_context(
            candidate,
            self._routing_context_reader,
            None if target is None else target.page_hint,
        )
        self._last_routing_context = routing_context
        if workflow == "document.save":
            preflight = save_preflight_result(
                intent_or_operation_id,
                resolve_only=resolve_only,
                allow_document_change=allow_document_change,
            )
            if preflight is not None:
                return preflight
            native_save = execute_native_save(candidate.window_handle)
            if native_save is None:
                raise HwpLiveError(
                    "한컴 네이티브 일반 저장 검증기를 사용할 수 없습니다"
                )
            return save_result(intent_or_operation_id, native_save)
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
        preflight_inputs = WorkflowPreflightInputs(
            target, data, assets, inputs, layout, recipe
        )
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
        atomic_resolution = (
            resolve_operation(intent_or_operation_id) if workflow is None else None
        )
        natural_execution = prepare_natural_route(
            natural_route,
            preflight_inputs,
            atomic_resolution,
        )
        if natural_execution.blocked_result is not None:
            return natural_execution.blocked_result
        atomic_rescue = (
            natural_execution.workflow is None
            and natural_execution.selection is not None
        )
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
            if not resolve_only and allow_document_change and layout is not None:
                require_writable_document(
                    self._unsafe_selectors,
                    candidate.selector,
                    reobserve=lambda: (
                        read_native_snapshot(candidate.window_handle) is not None
                    ),
                )

            insert_after_control = None
            if target is not None and target.binding == "below_selection":
                if (
                    layout is None
                    or layout.target != "current"
                    or target.kind not in {"table", "control"}
                    or target.control_instance_id is None
                ):
                    raise HwpLiveError(
                        "below_selection 레이아웃 대상에는 target=current인 표 개체 ID가 필요합니다"
                    )
                matched = tuple(
                    control
                    for control in routing_page.controls
                    if control.instance_id == target.control_instance_id
                )
                if len(matched) != 1:
                    raise HwpLiveError(
                        "레이아웃을 바로 뒤에 삽입할 기준 표를 정확히 찾지 못했습니다"
                    )
                insert_after_control = (
                    matched[0].anchor,
                    target.control_instance_id,
                    routing_page.page,
                )

            def execute_layout() -> OperationResult:
                return operate_layout(
                    candidate,
                    intent_or_operation_id,
                    layout,
                    resolve_only=resolve_only,
                    allow_document_change=allow_document_change,
                    expected_cursor=expected_cursor,
                    unsafe_selectors=self._unsafe_selectors,
                    guard=guard,
                    atomic=effective_policy.atomic,
                    style_list=None,
                    insert_after_control=insert_after_control,
                )

            history_operation = (
                "document.insert_layout"
                if resolution.workflow_id == "document.insert_layout"
                else "document.append_layout"
            )
            result = (
                execute_managed_layout_edit(
                    candidate,
                    self._live_edit_history,
                    history_operation,
                    execute_layout,
                    require_undo_entry=(
                        inputs.get("file_checkpoint_history")
                        == "i09_current_format_insert"
                    ),
                )
                if not resolve_only and allow_document_change and layout is not None
                else execute_layout()
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
        if session_id not in self._sessions:
            raise HwpLiveError("유효한 한컴 라이브 세션이 아닙니다")
        if self._session_id != session_id:
            raise HwpLiveError("요청한 세션에는 현재 작업의 라우팅 컨텍스트가 없습니다")
        if self._last_routing_context is None:
            raise HwpLiveError("최근 hwp_operate 빠른 구조 컨텍스트가 없습니다")
        return self._last_routing_context
