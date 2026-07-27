from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — all COM-dispatched operation entry points share one connection boundary.

import asyncio  # noqa: F401 -- # noqa: ANYIO_OK (concurrent Future bridge)
from collections.abc import AsyncGenerator, Awaitable, Callable
from concurrent.futures import Future
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic_ns
from typing import Final, final

from anyio import CancelScope, to_thread

from hwp_errors import HwpLiveError
from hwp_layout_preflight import LayoutPreflightResult
from hwp_live_bridge import HancomBridge, operation_recovery_scope
from hwp_live_contract import (
    ConnectedDocument,
    DocumentStyleList,
    LayoutPlan,
    LiveContext,
    PreviewResult,
)
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
    OperationPosition,
    OperationResult,
    TextMatchCandidate,
    canonical_workflow,
)
from hwp_operation_idempotency import OperationIdempotency, OperationTicket
from hwp_operation_journal import OperationJournal
from hwp_operation_registry import operation_registry
from hwp_operation_verification import enforce_operation_verification
from hwp_live_text_patch_contract import TextPatchRequest, TextPatchTarget
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
# OperationResult.native_protocol is the public compatibility name for the
# execution path's minimum required bridge protocol, not the connected DLL's
# runtime ProtocolVersion.
_TEXT_PATCH_MINIMUM_NATIVE_PROTOCOL: Final = 11


CleanupCompletionWaiter = Callable[
    [tuple[Future[None], ...], float],
    Awaitable[None],
]


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
                message="범위·검색·표 셀 text.patch에는 확인할 기존 텍스트가 필요합니다",
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

        def execute_started_patch() -> OperationResult:
            try:
                patched = self._bridge.patch_text(connected.session_id, request)
                before = patched.before
                after = patched.after
                result = OperationResult(
                    request_id=inputs.request_id,
                    status="executed",
                    changed=True,
                    query=intent,
                    registry_entries=operation_registry().count,
                    lookup_microseconds=0,
                    message="본문 text.patch를 적용하고 변경 범위와 서식을 다시 읽어 검증했습니다",
                    execution_mode="native_in_process",
                    native_protocol=_TEXT_PATCH_MINIMUM_NATIVE_PROTOCOL,
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
                )
            except NativeActionFailure as failure:
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
                            if request.expected_text is None
                            else request.expected_text,
                        ),
                        message="일치하는 본문이 여러 개여서 변경하지 않았습니다. occurrence를 지정하세요",
                        execution_mode="native_in_process",
                        native_protocol=_TEXT_PATCH_MINIMUM_NATIVE_PROTOCOL,
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
                        native_protocol=_TEXT_PATCH_MINIMUM_NATIVE_PROTOCOL,
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
                        minimum_native_protocol=(_TEXT_PATCH_MINIMUM_NATIVE_PROTOCOL),
                    ).model_copy(update={"request_id": inputs.request_id})
            except HwpLiveError as error:
                result = transport_error_result(inputs, error, intent=intent)
            return self._idempotency.commit(
                ticket,
                enforce_operation_verification(canonical_workflow(inputs), result),
            )

        async with self._idempotency.execution(ticket):
            return await self._dispatcher.run_mutation(execute_started_patch)

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
    ) -> DocumentStyleList:
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

        def execute_started_mutation() -> OperationResult:
            with operation_recovery_scope(
                None if ticket is None else ticket.request_id
            ):
                try:
                    result = self._bridge.operate(
                        connected.session_id,
                        intent,
                        inputs.parameters,
                        resolve_only=reconcile,
                        allow_document_change=not reconcile,
                        use_defaults=inputs.use_defaults,
                        expected_cursor=expected_cursor,
                        workflow=canonical_workflow(inputs),
                        target=inputs.target,
                        data=inputs.data,
                        assets=inputs.assets,
                        policy=inputs.policy,
                        postconditions=inputs.postconditions,
                        layout=inputs.layout,
                        recipe=inputs.recipe,
                    )
                except NativeActionFailure as failure:
                    result = native_action_failure_result(intent, failure)
                except HwpLiveError as error:
                    result = transport_error_result(inputs, error, intent=intent)
            result = enforce_operation_verification(canonical_workflow(inputs), result)
            committed = self._idempotency.commit(
                ticket,
                result.model_copy(update={"request_id": inputs.request_id}),
            )
            return self._immediate_save_reconciliation(
                ticket,
                committed,
                connected.document.selector,
            )

        async with self._idempotency.execution(ticket):
            return await self._dispatcher.run_mutation(
                execute_started_mutation,
            )
