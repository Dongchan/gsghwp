from __future__ import annotations

from contextlib import suppress
from typing import final

from hwp_errors import HwpLiveError
from hwp_live_bridge import HancomBridge
from hwp_live_contract import (
    ConnectedDocument,
    DocumentStyleList,
    LiveContext,
    MutationResult,
    OpenDocument,
    PreviewResult,
)
from hwp_live_native_action_contract import NativeActionFailure
from hwp_live_session_candidate import select_operation_document
from hwp_live_structure_contract import DocumentStructure, FastPageInspection
from hwp_mcp_dispatch import McpThreadDispatcher
from hwp_mcp_result_envelope import transport_error_result
from hwp_native_failure_result import native_action_failure_result
from hwp_operation_contract import (
    HwpOperateGuards,
    HwpOperateInputs,
    OperationResult,
    canonical_workflow,
)
from hwp_operation_idempotency import OperationIdempotency, OperationTicket
from hwp_operation_journal import OperationJournal


@final
class HwpOperationExecutor:
    __slots__ = (
        "_bridge",
        "_dispatcher",
        "_idempotency",
        "operation_document",
        "operation_session_id",
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
        self.operation_document: OpenDocument | None = None
        self.operation_session_id: str | None = None

    async def ensure_connection(
        self,
        document_selector: str | None,
    ) -> ConnectedDocument:
        listing = await self._dispatcher.run(self._bridge.list_open_documents)
        selected = select_operation_document(listing, document_selector)
        if self.operation_session_id is not None:
            assert self.operation_document is not None
            if self.operation_document.selector == selected.selector:
                self.operation_document = selected
            else:
                with suppress(HwpLiveError):
                    _ = await self._dispatcher.run(
                        self._bridge.disconnect,
                        self.operation_session_id,
                    )
                self.operation_session_id = None
                self.operation_document = None
        if self.operation_session_id is None:
            connected = await self._dispatcher.run(
                self._bridge.connect, selected.selector
            )
            self.operation_session_id = connected.session_id
            self.operation_document = connected.document
        assert self.operation_session_id is not None
        assert self.operation_document is not None
        return ConnectedDocument(
            session_id=self.operation_session_id,
            document=self.operation_document,
        )

    async def inspect_context(self, document_selector: str | None) -> LiveContext:
        connected = await self.ensure_connection(document_selector)
        return await self._dispatcher.run(
            self._bridge.context,
            connected.session_id,
        )

    async def replace_selected_text(
        self,
        document_selector: str | None,
        replacement: str,
    ) -> MutationResult:
        connected = await self.ensure_connection(document_selector)
        context = await self._dispatcher.run(
            self._bridge.context,
            connected.session_id,
        )
        return await self._dispatcher.run(
            self._bridge.replace_selection,
            connected.session_id,
            context.selection,
            context.selected_text,
            replacement,
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
            _ = await self.ensure_connection(inputs.document)
        except HwpLiveError as error:
            return transport_error_result(
                inputs,
                error,
                intent=intent,
                mutation_started=False,
            )
        assert self.operation_session_id is not None
        assert self.operation_document is not None
        prepared = self._idempotency.prepare(
            self.operation_document,
            intent,
            inputs,
            guards,
        )
        if isinstance(prepared, OperationResult):
            return prepared
        ticket: OperationTicket | None = prepared
        reconcile = ticket is not None and ticket.action == "reconcile"
        with self._idempotency.execution(ticket):
            try:
                result = await self._dispatcher.run(
                    self._bridge.operate,
                    self.operation_session_id,
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
        return self._idempotency.commit(
            ticket,
            result.model_copy(update={"request_id": inputs.request_id}),
        )
