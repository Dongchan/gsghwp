from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — all COM-dispatched operation entry points share one connection boundary.

import asyncio  # noqa: F401 -- # noqa: ANYIO_OK (concurrent Future bridge)
from collections.abc import AsyncGenerator, Awaitable, Callable
from concurrent.futures import Future
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic_ns
from typing import Final, Literal, cast, final

from anyio import CancelScope, to_thread

from hwp_errors import HwpLiveError
from hwp_layout_preflight import LayoutPreflightResult
from hwp_live_bridge import HancomBridge, operation_recovery_scope
from hwp_live_contract import (
    ConnectedDocument,
    LayoutPlan,
    LiveContext,
    PreviewResult,
    StyleReadOutcome,
)
from hwp_live_grounding import HwpGroundingReport, HwpGroundingRequest
from hwp_pageplan_contract import canonical_page_plan_sha256
from hwp_pageplan_g04_contract import G04ApplyRequest, G04ApplyResponse, G04Error
from hwp_live_native_action_contract import NativeActionFailure
from hwp_live_native_dispatch_scope import (
    PublicNativeDispatchScope,
    public_native_dispatch_scope,
)
from hwp_live_structure_contract import DocumentStructure, FastPageInspection
from hwp_mcp_dispatch import McpThreadDispatcher
from hwp_mcp_result_envelope import transport_error_result
from hwp_native_failure_result import native_action_failure_result
from hwp_operation_contract import (
    HwpOperateGuards,
    HwpOperateInputs,
    OperationPhaseTiming,
    OperationPosition,
    OperationResult,
    TextMatchCandidate,
    canonical_workflow,
)
from hwp_operation_idempotency import OperationIdempotency, OperationTicket
from hwp_operation_journal import OperationJournal
from hwp_operation_local_precondition import (
    is_local_target_precondition_failure,
    is_local_text_precondition_failure,
    local_precondition_retry_result,
    reinterpreted_operate_inputs,
    reinterpreted_text_patch,
)
from hwp_operation_registry import operation_registry
from hwp_operation_verification import enforce_operation_verification
from hwp_live_text_patch_contract import (
    TextPatchPlanGuard,
    TextPatchRequest,
    TextPatchTarget,
    text_patch_minimum_protocol,
)
from hwp_mcp_session_lifetime import PublicSessionLifetime, public_session_lifetime


@dataclass(slots=True)
class _PublicToolSessionScope:
    owner_id: int
    lifetime: PublicSessionLifetime
    session_ids: set[str] = field(default_factory=set)


_PUBLIC_TOOL_SESSION_SCOPE: ContextVar[_PublicToolSessionScope | None] = ContextVar(
    "hwp_public_tool_session_scope",
    default=None,
)
_PUBLIC_CLEANUP_GRACE_SECONDS: Final = 0.05
_TEXT_PATCH_APPLIED_MESSAGE: Final = (
    "본문 text.patch를 적용하고 변경 범위와 서식을 다시 읽어 검증했습니다"
)
CleanupCompletionWaiter = Callable[
    [tuple[Future[None], ...], float],
    Awaitable[None],
]


def _text_patch_message(notice: str) -> str:
    """The applied-and-verified line, plus whatever else the caller must know.

    A notice never replaces the message: the patch was applied either way, and
    what the notice reports is something alongside it, such as this edit having
    no MCP undo entry.
    """
    if not notice:
        return _TEXT_PATCH_APPLIED_MESSAGE
    return f"{_TEXT_PATCH_APPLIED_MESSAGE}. {notice}"


def _consume_cleanup_result(future: asyncio.Future[None]) -> None:
    if future.cancelled():
        return
    try:
        _ = future.exception()
    except (asyncio.CancelledError, Exception):
        return


async def _wait_for_cleanup_completion(
    futures: tuple[Future[None], ...],
    timeout_seconds: float,
) -> None:
    wrapped = tuple(asyncio.wrap_future(future) for future in futures)
    for future in wrapped:
        future.add_done_callback(_consume_cleanup_result)
    _ = await asyncio.wait(wrapped, timeout=timeout_seconds)


async def _observe_best_effort_cleanup(
    futures: tuple[Future[None], ...],
    *,
    grace_seconds: float = _PUBLIC_CLEANUP_GRACE_SECONDS,
    clock_ns: Callable[[], int] = monotonic_ns,
    wait_for_completion: CleanupCompletionWaiter = _wait_for_cleanup_completion,
) -> None:
    deadline_ns = clock_ns() + int(grace_seconds * 1_000_000_000)
    pending = tuple(future for future in futures if not future.done())
    if not pending:
        return
    remaining_ns = deadline_ns - clock_ns()
    if remaining_ns <= 0:
        return
    await wait_for_completion(pending, remaining_ns / 1_000_000_000)


def _text_match_candidates(
    encoded: str,
    matched_text: str,
) -> tuple[TextMatchCandidate, ...]:
    candidates: list[TextMatchCandidate] = []
    for occurrence, item in enumerate(encoded.split("|"), start=1):
        endpoints = item.split("-")
        if len(endpoints) != 2:
            raise HwpLiveError("네이티브 text.patch 후보 범위 형식이 올바르지 않습니다")
        coordinates: list[tuple[int, int, int]] = []
        for endpoint in endpoints:
            fields = endpoint.split(":")
            if len(fields) != 3:
                raise HwpLiveError(
                    "네이티브 text.patch 후보 좌표 형식이 올바르지 않습니다"
                )
            try:
                coordinate = tuple(int(field) for field in fields)
            except ValueError as error:
                raise HwpLiveError(
                    "네이티브 text.patch 후보 좌표가 숫자가 아닙니다"
                ) from error
            if len(coordinate) != 3 or min(coordinate) < 0:
                raise HwpLiveError("네이티브 text.patch 후보 좌표가 올바르지 않습니다")
            coordinates.append(coordinate)
        start, end = coordinates
        candidates.append(
            TextMatchCandidate(
                occurrence=occurrence,
                start=OperationPosition(
                    list_id=start[0],
                    paragraph=start[1],
                    character=start[2],
                ),
                end=OperationPosition(
                    list_id=end[0],
                    paragraph=end[1],
                    character=end[2],
                ),
                matched_text=matched_text,
            )
        )
    return tuple(candidates)


@final
class HwpOperationExecutor:
    __slots__ = (
        "_bridge",
        "_dispatcher",
        "_idempotency",
    )

    def __init__(
        self,
        bridge: HancomBridge,
        dispatcher: McpThreadDispatcher,
        operation_journal: OperationJournal | None,
    ) -> None:
        self._bridge = bridge
        self._dispatcher = dispatcher
        self._idempotency = OperationIdempotency(
            OperationJournal() if operation_journal is None else operation_journal
        )

    def _immediate_save_reconciliation(
        self,
        ticket: OperationTicket | None,
        result: OperationResult,
        document_selector: str,
    ) -> OperationResult:
        if (
            ticket is None
            or ticket.request_id is None
            or ticket.operation not in {"document.save", "document.save_reopen_verify"}
            or result.status != "transport_error"
        ):
            return result
        reconciled = self._idempotency.status_without_connection(
            ticket.request_id,
            ticket.document_path,
        )
        if reconciled is None:
            return result
        if (
            reconciled.save_fingerprint_verified is True
            and reconciled.disk_persistence_verified is True
            and reconciled.verified is True
            and reconciled.reconcile_required is False
        ):
            try:
                _ = self._bridge.reconcile_confirmed_save(
                    document_selector,
                    ticket.request_id,
                )
            except AttributeError:
                pass
        return reconciled

    def _release_confirmed_save_close_block(
        self,
        ticket: OperationTicket | None,
        result: OperationResult,
        session_id: str,
    ) -> None:
        """디스크 지문으로 확정된 저장이 닫기 차단을 남기지 않게 한다.

        ``SaveStateMachine`` 은 네이티브 readback 이 저장 전후 문서 지문을
        대조해야 "verified"에 닿는다. 엔진이 직렬화하지 못하는 크기의
        문서에서는 그 지문이 아예 없어 매번 미확정이 남고, 해제 경로는
        도달할 수 없는 저장을 한 번 더 요구한다. 반증은 같은 응답 안에
        있다 — 이 자리에 온 결과가 6조건 파일 지문 증명을 통과했다면
        저장은 이미 디스크에서 확정됐다.
        """
        if (
            ticket is None
            or ticket.operation not in {"document.save", "document.save_reopen_verify"}
            or result.save_fingerprint_verified is not True
            or result.disk_persistence_verified is not True
            or result.verified is not True
            or result.reconcile_required is not False
        ):
            return
        try:
            _ = self._bridge.release_confirmed_save_close_block(session_id)
        except AttributeError:
            pass

    async def ensure_connection(
        self,
        document_selector: str | None,
    ) -> ConnectedDocument:
        scope = _PUBLIC_TOOL_SESSION_SCOPE.get()
        active_scope = (
            scope if scope is not None and scope.owner_id == id(self) else None
        )
        persistent = (
            None
            if active_scope is None or active_scope.lifetime == "disconnect"
            else active_scope.lifetime == "persistent"
        )
        connected = await self._dispatcher.run_mutation(
            self._bridge.ensure_connection,
            document_selector,
            persistent=persistent,
        )
        if active_scope is not None and active_scope.lifetime == "stateless":
            active_scope.session_ids.add(connected.session_id)
        return connected

    @asynccontextmanager
    async def public_tool_session_scope(
        self,
        tool_name: str,
    ) -> AsyncGenerator[None]:
        scope = _PublicToolSessionScope(
            owner_id=id(self),
            lifetime=public_session_lifetime(tool_name),
        )
        dispatch_scope: PublicNativeDispatchScope | None = None
        process_ids: frozenset[int] = frozenset()
        try:
            with public_native_dispatch_scope(
                self._bridge,
                on_activity=self._dispatcher.notify_public_activity,
            ) as active_dispatch_scope:
                dispatch_scope = active_dispatch_scope
                token = _PUBLIC_TOOL_SESSION_SCOPE.set(scope)
                try:
                    yield
                finally:
                    _PUBLIC_TOOL_SESSION_SCOPE.reset(token)
        finally:
            with CancelScope(shield=True):
                if dispatch_scope is not None:
                    process_ids = dispatch_scope.process_ids()
                    await _observe_best_effort_cleanup(dispatch_scope.close())
                if scope.lifetime == "stateless":
                    await self._release_public_tool_sessions(
                        scope.session_ids,
                        process_ids,
                    )

    async def _release_public_tool_sessions(
        self,
        session_ids: set[str],
        process_ids: frozenset[int],
    ) -> None:
        release_session_ids = tuple(session_ids)

        def release_sessions() -> None:
            first_error: Exception | None = None
            for session_id in release_session_ids:
                try:
                    _ = self._bridge.release_transient_connection(session_id)
                except Exception as error:
                    if first_error is None:
                        first_error = error
            if first_error is not None:
                raise first_error

        if release_session_ids:
            try:
                session_cleanup = self._dispatcher.submit_retriable_cleanup(
                    process_ids,
                    release_sessions,
                )
            except Exception:
                session_cleanup = None
            if session_cleanup is not None:
                await _observe_best_effort_cleanup((session_cleanup,))

        try:
            if process_ids:
                self._dispatcher.schedule_idle_cleanup(
                    process_ids,
                    self._bridge.release_idle_references,
                )
            else:
                idle_cleanup = self._dispatcher.submit_retriable_cleanup(
                    (),
                    self._bridge.release_idle_references,
                    process_ids,
                )
                await _observe_best_effort_cleanup((idle_cleanup,))
        except Exception:
            return

    async def inspect_context(self, document_selector: str | None) -> LiveContext:
        connected = await self.ensure_connection(document_selector)
        return await self._dispatcher.run(
            self._bridge.context,
            connected.session_id,
        )

    async def ground_document(
        self,
        document_selector: str | None,
        request: HwpGroundingRequest,
    ) -> HwpGroundingReport:
        connected = await self.ensure_connection(document_selector)
        return await self._dispatcher.run(
            self._bridge.ground_document,
            connected.session_id,
            request,
        )

    async def preflight_layout(
        self,
        document_selector: str | None,
        plan: LayoutPlan,
    ) -> LayoutPreflightResult:
        connected = await self.ensure_connection(document_selector)
        return await self._dispatcher.run(
            self._bridge.preflight_layout,
            connected.session_id,
            plan,
        )

    async def apply_page_plan(
        self,
        request: G04ApplyRequest,
    ) -> G04ApplyResponse:
        try:
            connected = await self.ensure_connection(request.document_selector)
        except HwpLiveError as error:
            plan = request.compiled.plan
            return G04ApplyResponse(
                status="rejected",
                operation_id=request.operation_id,
                candidate=plan.candidate,
                plan_sha256=canonical_page_plan_sha256(plan),
                source_manifest_sha256=request.compiled.source_manifest_sha256,
                error=G04Error(
                    code="DOCUMENT_UNAVAILABLE",
                    message=str(error),
                ),
            )
        return await self._dispatcher.run_mutation(
            self._bridge.apply_page_plan,
            connected.session_id,
            request,
        )

    async def replace_selected_text(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        replacement: str,
    ) -> OperationResult:
        try:
            connected = await self.ensure_connection(inputs.document)
            context = await self._dispatcher.run(
                self._bridge.context,
                connected.session_id,
            )
        except HwpLiveError as error:
            return transport_error_result(
                inputs,
                error,
                intent=intent,
                mutation_started=False,
            )
        if not context.selection.selected:
            return OperationResult(
                request_id=inputs.request_id,
                status="needs_input",
                query=intent,
                registry_entries=operation_registry().count,
                lookup_microseconds=0,
                required_inputs=("inputs.target",),
                message="현재 선택된 본문 범위가 없습니다",
                verified=False,
                partial_mutation=False,
                retry_safe=True,
            )
        return await self.patch_text(
            intent,
            inputs,
            TextPatchRequest(
                target=TextPatchTarget(kind="current"),
                expected_text=context.selected_text,
                replacement=replacement,
            ),
        )

    async def patch_text(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        request: TextPatchRequest,
    ) -> OperationResult:
        if request.target.kind != "current" and request.expected_text is None:
            return OperationResult(
                request_id=inputs.request_id,
                status="schema_conflict",
                query=intent,
                registry_entries=operation_registry().count,
                lookup_microseconds=0,
                message=(
                    f'target.kind="{request.target.kind}"에는 최상위 expected_text가 '
                    "필요합니다. find는 그 문자열을 문서에서 찾고, range·table_cell은 "
                    "그 자리의 현재 원문이 같은지 확인한 뒤에만 바꿉니다"
                ),
                verified=False,
                partial_mutation=False,
                retry_safe=True,
            )
        if (
            request.target.kind == "current"
            and request.expected_text is None
            and not request.replacement
        ):
            return OperationResult(
                request_id=inputs.request_id,
                status="schema_conflict",
                query=intent,
                registry_entries=operation_registry().count,
                lookup_microseconds=0,
                message="선택이 없는 현재 커서에는 빈 문자열을 삽입할 수 없습니다",
                verified=False,
                partial_mutation=False,
                retry_safe=True,
            )
        minimum_protocol = text_patch_minimum_protocol(request)
        try:
            connected = await self.ensure_connection(inputs.document)
        except HwpLiveError as error:
            return transport_error_result(
                inputs,
                error,
                intent=intent,
                mutation_started=False,
            )
        prepared = await to_thread.run_sync(
            self._idempotency.prepare,
            connected.document,
            intent,
            inputs,
            None,
        )
        if isinstance(prepared, OperationResult):
            return prepared
        ticket: OperationTicket | None = prepared

        def attempt_patch(
            attempted: TextPatchRequest,
        ) -> tuple[OperationResult, NativeActionFailure | None]:
            native_failure: NativeActionFailure | None = None
            try:
                patched = self._bridge.patch_text(connected.session_id, attempted)
                before = patched.before
                after = patched.after
                result = OperationResult(
                    request_id=inputs.request_id,
                    status="executed",
                    changed=True,
                    query=intent,
                    registry_entries=operation_registry().count,
                    lookup_microseconds=0,
                    message=_text_patch_message(patched.notice),
                    execution_mode="native_in_process",
                    native_protocol=minimum_protocol,
                    verification="native_operation_specific_readback",
                    verified=True,
                    commands_executed=patched.native.commands_executed,
                    native_actions_executed=patched.native.actions_executed,
                    native_elapsed_microseconds=patched.native.elapsed_microseconds,
                    current_page=after.current_page,
                    page_count=after.page_count,
                    modified=after.modified,
                    partial_mutation=False,
                    retry_safe=True,
                    commands_completed=patched.native.commands_executed,
                    cursor_before=OperationPosition(
                        list_id=before.cursor.list_id,
                        paragraph=before.cursor.paragraph,
                        character=before.cursor.character,
                    ),
                    cursor_after=OperationPosition(
                        list_id=after.cursor.list_id,
                        paragraph=after.cursor.paragraph,
                        character=after.cursor.character,
                    ),
                    # Straight from the bridge's own call results. null means
                    # this patch wrote no checkpoint at all.
                    document_checkpoint_capture=cast(
                        Literal["document_file", "encoded_block"] | None,
                        patched.checkpoint_evidence.capture_method or None,
                    ),
                    document_identity_restored=(
                        patched.checkpoint_evidence.identity_restored
                        if patched.checkpoint_evidence.capture_method
                        else None
                    ),
                )
            except NativeActionFailure as failure:
                native_failure = failure
                if failure.code == "AMBIGUOUS_TEXT_MATCH":
                    result = OperationResult(
                        request_id=inputs.request_id,
                        status="ambiguous",
                        query=intent,
                        registry_entries=operation_registry().count,
                        lookup_microseconds=0,
                        text_candidates=_text_match_candidates(
                            failure.location,
                            ""
                            if attempted.expected_text is None
                            else attempted.expected_text,
                        ),
                        message="일치하는 본문이 여러 개여서 변경하지 않았습니다. occurrence를 지정하세요",
                        execution_mode="native_in_process",
                        native_protocol=minimum_protocol,
                        verification="native_operation_specific_readback",
                        verified=False,
                        commands_executed=0,
                        partial_mutation=False,
                        retry_safe=True,
                        commands_completed=0,
                    )
                elif failure.code in {
                    "TEXT_NOT_FOUND",
                    "TEXT_OCCURRENCE_NOT_FOUND",
                }:
                    result = OperationResult(
                        request_id=inputs.request_id,
                        status="not_found",
                        query=intent,
                        registry_entries=operation_registry().count,
                        lookup_microseconds=0,
                        message=str(failure),
                        execution_mode="native_in_process",
                        native_protocol=minimum_protocol,
                        verification="native_operation_specific_readback",
                        verified=False,
                        commands_executed=0,
                        partial_mutation=False,
                        retry_safe=True,
                        commands_completed=0,
                    )
                else:
                    result = native_action_failure_result(
                        intent,
                        failure,
                        minimum_native_protocol=minimum_protocol,
                    ).model_copy(update={"request_id": inputs.request_id})
            except HwpLiveError as error:
                result = transport_error_result(inputs, error, intent=intent)
            return result, native_failure

        def execute_started_patch() -> OperationResult:
            result, failure = attempt_patch(request)
            # 대상 국소 전제조건 계약: 지목한 자리의 기대 원문이 어긋나 편집 전에
            # 멈췄다면, 호출자가 준 expected_text 로 한 번만 다시 해석해 재시도한다.
            # 티켓을 새로 뽑지 않으므로 operation_id 는 그대로다.
            if failure is not None and is_local_text_precondition_failure(failure):
                reinterpreted = reinterpreted_text_patch(request)
                if reinterpreted is not None:
                    retried, _ = attempt_patch(reinterpreted)
                    result = local_precondition_retry_result(result, retried)
            return self._idempotency.commit(
                ticket,
                enforce_operation_verification(canonical_workflow(inputs), result),
            )

        async with self._idempotency.execution(ticket):
            return await self._dispatcher.run_mutation(execute_started_patch)

    async def patch_text_batch(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        requests: tuple[TextPatchRequest, ...],
        plan_guard: TextPatchPlanGuard | None = None,
    ) -> OperationResult:
        minimum_protocol: Literal[11, 12] = (
            12
            if any(text_patch_minimum_protocol(request) == 12 for request in requests)
            else 11
        )
        try:
            connected = await self.ensure_connection(inputs.document)
        except HwpLiveError as error:
            return transport_error_result(
                inputs,
                error,
                intent=intent,
                mutation_started=False,
            )
        if plan_guard is not None and (
            connected.document.document_id != plan_guard.document_id
            or connected.document.full_name.casefold() != plan_guard.full_name.casefold()
        ):
            return transport_error_result(
                inputs,
                HwpLiveError(
                    "text.patch plan document identity가 현재 문서와 다릅니다",
                    mutation_started=False,
                    safe_to_repeat=True,
                ),
                intent=intent,
                mutation_started=False,
            )
        prepared = await to_thread.run_sync(
            self._idempotency.prepare,
            connected.document,
            intent,
            inputs,
            None,
        )
        if isinstance(prepared, OperationResult):
            return prepared
        ticket: OperationTicket | None = prepared

        def execute_started_batch() -> OperationResult:
            try:
                patched = self._bridge.patch_text_batch(
                    connected.session_id,
                    requests,
                    plan_guard,
                )
                before = patched.before
                after = patched.after
                result = OperationResult(
                    request_id=inputs.request_id,
                    status="executed",
                    changed=True,
                    query=intent,
                    registry_entries=operation_registry().count,
                    lookup_microseconds=0,
                    message=_text_patch_message(patched.notice),
                    execution_mode="native_in_process",
                    native_protocol=minimum_protocol,
                    verification="native_operation_specific_readback",
                    verified=True,
                    commands_executed=patched.native.commands_executed,
                    native_actions_executed=patched.native.actions_executed,
                    native_elapsed_microseconds=patched.native.elapsed_microseconds,
                    phase_timings=tuple(
                        OperationPhaseTiming(
                            phase=timing.phase,
                            source=timing.source,
                            elapsed_microseconds=timing.elapsed_microseconds,
                        )
                        for timing in patched.phase_timings
                    ),
                    current_page=after.current_page,
                    page_count=after.page_count,
                    modified=after.modified,
                    partial_mutation=False,
                    retry_safe=True,
                    commands_completed=patched.native.commands_executed,
                    cursor_before=OperationPosition(
                        list_id=before.cursor.list_id,
                        paragraph=before.cursor.paragraph,
                        character=before.cursor.character,
                    ),
                    cursor_after=OperationPosition(
                        list_id=after.cursor.list_id,
                        paragraph=after.cursor.paragraph,
                        character=after.cursor.character,
                    ),
                    document_checkpoint_capture=cast(
                        Literal["document_file", "encoded_block"] | None,
                        patched.checkpoint_evidence.capture_method or None,
                    ),
                    document_identity_restored=(
                        patched.checkpoint_evidence.identity_restored
                        if patched.checkpoint_evidence.capture_method
                        else None
                    ),
                )
            except NativeActionFailure as failure:
                result = native_action_failure_result(
                    intent,
                    failure,
                    minimum_native_protocol=minimum_protocol,
                ).model_copy(
                    update={
                        "request_id": inputs.request_id,
                        "phase_timings": tuple(
                            OperationPhaseTiming(
                                phase=phase,
                                source=source,
                                elapsed_microseconds=elapsed,
                            )
                            for phase, source, elapsed in failure.phase_timings
                        ),
                    }
                )
            except HwpLiveError as error:
                result = transport_error_result(
                    inputs,
                    error,
                    intent=intent,
                ).model_copy(
                    update={
                        "phase_timings": tuple(
                            OperationPhaseTiming(
                                phase=phase,
                                source=source,
                                elapsed_microseconds=elapsed,
                            )
                            for phase, source, elapsed in error.phase_timings
                        )
                    }
                )
            return self._idempotency.commit(
                ticket,
                enforce_operation_verification(canonical_workflow(inputs), result),
            )

        async with self._idempotency.execution(ticket):
            return await self._dispatcher.run_mutation(execute_started_batch)

    async def get_operation_status(
        self,
        operation_id: str,
        document_selector: str | None,
    ) -> OperationResult:
        inputs = HwpOperateInputs(
            request_id=operation_id,
            document=document_selector,
        )
        offline = await to_thread.run_sync(
            self._idempotency.status_without_connection,
            operation_id,
            document_selector,
        )
        if offline is not None:
            if (
                offline.save_fingerprint_verified is True
                and offline.disk_persistence_verified is True
                and offline.verified is True
                and offline.reconcile_required is False
            ):
                recovery_selector = (
                    document_selector or offline.saved_path or offline.reopened_path
                )
                _ = await to_thread.run_sync(
                    self._bridge.reconcile_confirmed_save,
                    recovery_selector,
                    operation_id,
                )
            return offline
        try:
            connected = await self.ensure_connection(document_selector)
        except HwpLiveError as error:
            return transport_error_result(
                inputs,
                error,
                intent="operation status",
                mutation_started=False,
            )
        return await to_thread.run_sync(
            self._idempotency.status,
            connected.document,
            operation_id,
        )

    async def inspect_styles(
        self,
        document_selector: str | None,
    ) -> StyleReadOutcome:
        connected = await self.ensure_connection(document_selector)
        return await self._dispatcher.run(
            self._bridge.styles,
            connected.session_id,
        )

    async def inspect_page_fast(
        self,
        document_selector: str | None,
        page: int,
        include_cells: bool,
    ) -> FastPageInspection:
        connected = await self.ensure_connection(document_selector)
        return await self._dispatcher.run(
            self._bridge.inspect_page_fast,
            connected.session_id,
            page,
            include_cells=include_cells,
        )

    async def read_content_revision(
        self,
        document_selector: str | None,
    ) -> str:
        connected = await self.ensure_connection(document_selector)
        return await self._dispatcher.run(
            self._bridge.content_revision,
            connected.session_id,
        )

    async def inspect_structure(
        self,
        document_selector: str | None,
        page: int,
    ) -> DocumentStructure:
        connected = await self.ensure_connection(document_selector)
        return await self._dispatcher.run(
            self._bridge.structure,
            connected.session_id,
            page,
        )

    async def render_page(
        self,
        document_selector: str | None,
        page: int,
        dpi: int,
    ) -> PreviewResult:
        connected = await self.ensure_connection(document_selector)
        return await self._dispatcher.run(
            self._bridge.render_page,
            connected.session_id,
            page,
            dpi,
        )

    async def read_table_structure(
        self,
        document_path: str | None,
        page: int,
    ) -> DocumentStructure:
        return await self.inspect_structure(document_path, page)

    async def execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationResult:
        expected_cursor = None
        if guards is not None and guards.cursor is not None:
            expected_cursor = (
                guards.cursor.list_id,
                guards.cursor.paragraph,
                guards.cursor.character,
            )
        try:
            connected = await self.ensure_connection(inputs.document)
        except HwpLiveError as error:
            return transport_error_result(
                inputs,
                error,
                intent=intent,
                mutation_started=False,
            )
        prepared = await to_thread.run_sync(
            self._idempotency.prepare, connected.document, intent, inputs, guards
        )
        if isinstance(prepared, OperationResult):
            return prepared
        ticket: OperationTicket | None = prepared
        reconcile = ticket is not None and ticket.action == "reconcile"

        def attempt_operation(attempted: HwpOperateInputs) -> OperationResult:
            try:
                return self._bridge.operate(
                    connected.session_id,
                    intent,
                    attempted.parameters,
                    resolve_only=reconcile,
                    allow_document_change=not reconcile,
                    use_defaults=attempted.use_defaults,
                    expected_cursor=expected_cursor,
                    workflow=canonical_workflow(attempted),
                    target=attempted.target,
                    data=attempted.data,
                    assets=attempted.assets,
                    policy=attempted.policy,
                    postconditions=attempted.postconditions,
                    layout=attempted.layout,
                    recipe=attempted.recipe,
                )
            except NativeActionFailure as failure:
                return native_action_failure_result(intent, failure)
            except HwpLiveError as error:
                return transport_error_result(attempted, error, intent=intent)

        def execute_started_mutation() -> OperationResult:
            with operation_recovery_scope(
                None if ticket is None else ticket.request_id
            ):
                result = attempt_operation(inputs)
                # 대상 국소 전제조건 계약: 지목한 개체 식별자가 문서와 맞지 않아
                # 아무것도 쓰지 않고 멈췄다면, 요청이 함께 들고 있는 대상 조건으로
                # 한 번만 다시 해석해 재시도한다. 같은 티켓 안에서 실행하므로
                # operation_id 는 그대로고 멱등성도 그대로다.
                #
                # reconcile 은 제외한다. 그 갈래는 이미 실행된 요청이 무엇을
                # 건드렸는지 되짚는 중이라, 다른 개체를 골라 오면 남의 편집을
                # 이 요청의 것으로 기록한다.
                if not reconcile and is_local_target_precondition_failure(result):
                    reinterpreted = reinterpreted_operate_inputs(inputs)
                    if reinterpreted is not None:
                        result = local_precondition_retry_result(
                            result,
                            attempt_operation(reinterpreted),
                        )
            result = enforce_operation_verification(canonical_workflow(inputs), result)
            committed = self._idempotency.commit(
                ticket,
                result.model_copy(update={"request_id": inputs.request_id}),
            )
            reconciled = self._immediate_save_reconciliation(
                ticket,
                committed,
                connected.document.selector,
            )
            self._release_confirmed_save_close_block(
                ticket,
                reconciled,
                connected.session_id,
            )
            return reconciled

        async with self._idempotency.execution(ticket):
            return await self._dispatcher.run_mutation(
                execute_started_mutation,
            )
