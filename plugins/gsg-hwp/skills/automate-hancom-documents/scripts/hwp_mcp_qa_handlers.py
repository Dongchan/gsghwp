from __future__ import annotations

from pathlib import Path
from typing import final

from hwp_document_rebuild import DocumentRebuildResult, rebuild_document
from hwp_live_bridge import HancomBridge
from hwp_live_bridge_contract import BridgeState, HancomDialogDismissResult, HancomWindowState
from hwp_live_contract import (
    LayoutPlan,
    LayoutResult,
    MutationResult,
    OpenDocumentList,
    PreviewResult,
    SelectionPosition,
)
from hwp_live_structure_contract import (
    TableCellUpdate,
    TableImageResult,
    TableImageUpdate,
    TableUpdateResult,
)
from hwp_live_workflow import (
    FolderImagePlan,
    FolderImageResult,
    TablePropagationPlan,
    TablePropagationResult,
)
from hwp_mcp_dispatch import McpThreadDispatcher
from hwp_office_table import OfficeTableSource
from hwp_official_api_live import OfficialApiLiveBatchResult, OfficialApiLiveCategory


@final
class McpQaHandlers:
    __slots__ = ("_bridge", "_dispatcher")

    def __init__(self, bridge: HancomBridge, dispatcher: McpThreadDispatcher) -> None:
        self._bridge = bridge
        self._dispatcher = dispatcher

    async def hwp_list_open_documents(self) -> OpenDocumentList:
        return await self._dispatcher.run(self._bridge.list_open_documents)

    async def hwp_inspect_window_state(self, window_handle: int) -> HancomWindowState:
        return await self._dispatcher.run(self._bridge.window_state, window_handle)

    async def hwp_dismiss_dialogs(
        self,
        window_handle: int,
    ) -> HancomDialogDismissResult:
        return await self._dispatcher.run(self._bridge.dismiss_dialogs, window_handle)

    async def hwp_run_official_api_batch(
        self,
        session_id: str,
        category: OfficialApiLiveCategory,
        start: int = 1,
        limit: int = 25,
    ) -> OfficialApiLiveBatchResult:
        return await self._dispatcher.run(
            self._bridge.run_official_api_batch,
            session_id,
            category,
            start,
            limit,
        )

    async def hwp_probe_official_api_batch(
        self,
        window_handle: int,
        category: OfficialApiLiveCategory,
        start: int = 1,
        limit: int = 25,
    ) -> OfficialApiLiveBatchResult:
        return await self._dispatcher.run(
            self._bridge.probe_official_api_batch,
            window_handle,
            category,
            start,
            limit,
        )

    async def hwp_probe_official_api_payload(
        self,
        window_handle: int,
        payload: str,
    ) -> str:
        return await self._dispatcher.run(
            self._bridge.probe_official_api_payload,
            window_handle,
            payload,
        )

    async def hwp_watch_state(
        self,
        session_id: str,
        page: int = 0,
        after_revision: int = 0,
        timeout_ms: int = 0,
    ) -> BridgeState:
        return await self._dispatcher.watch(
            self._bridge.watch_state,
            session_id,
            page,
            after_revision,
            timeout_ms,
        )

    async def hwp_replace_selection(
        self,
        session_id: str,
        expected_selection: SelectionPosition,
        expected_text: str,
        replacement: str,
    ) -> MutationResult:
        return await self._dispatcher.run(
            self._bridge.replace_selection,
            session_id,
            expected_selection,
            expected_text,
            replacement,
        )

    async def hwp_apply_layout(
        self,
        session_id: str,
        plan: LayoutPlan,
        expected_list_id: int,
        expected_paragraph: int,
        expected_character: int,
        expected_selected_text: str | None = None,
    ) -> LayoutResult:
        return await self._dispatcher.run(
            self._bridge.apply_layout,
            session_id,
            plan,
            (expected_list_id, expected_paragraph, expected_character),
            expected_selected_text,
        )

    async def hwp_update_table_cells(
        self,
        session_id: str,
        table_ref: str,
        state_token: str,
        updates: tuple[TableCellUpdate, ...],
    ) -> TableUpdateResult:
        return await self._dispatcher.run(
            self._bridge.update_table_cells,
            session_id,
            table_ref,
            state_token,
            updates,
        )

    async def hwp_insert_table_images(
        self,
        session_id: str,
        table_ref: str,
        state_token: str,
        images: tuple[TableImageUpdate, ...],
    ) -> TableImageResult:
        return await self._dispatcher.run(
            self._bridge.insert_table_images,
            session_id,
            table_ref,
            state_token,
            images,
        )

    async def hwp_import_office_table(
        self,
        session_id: str,
        page: int,
        table_ref: str,
        state_token: str,
        source: OfficeTableSource,
        target_start: str,
    ) -> TableUpdateResult:
        return await self._dispatcher.run(
            self._bridge.import_office_table,
            session_id,
            page,
            table_ref,
            state_token,
            source,
            target_start,
        )

    async def hwp_propagate_table_cells(
        self,
        session_id: str,
        plan: TablePropagationPlan,
    ) -> TablePropagationResult:
        return await self._dispatcher.run(
            self._bridge.propagate_table_cells,
            session_id,
            plan,
        )

    async def hwp_insert_folder_images(
        self,
        session_id: str,
        plan: FolderImagePlan,
    ) -> FolderImageResult:
        return await self._dispatcher.run(
            self._bridge.insert_folder_images,
            session_id,
            plan,
        )

    async def hwp_rebuild_document(
        self,
        source: str,
        output: str,
    ) -> DocumentRebuildResult:
        return await self._dispatcher.run(
            rebuild_document,
            Path(source),
            Path(output),
        )

    async def hwp_render_page(
        self,
        session_id: str,
        page: int = 0,
        dpi: int = 144,
    ) -> PreviewResult:
        return await self._dispatcher.run(self._bridge.render_page, session_id, page, dpi)
