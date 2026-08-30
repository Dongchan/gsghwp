from __future__ import annotations

from hwp_live_bridge_mixin import HancomBridgeMutationRuntime
from hwp_live_contract import (
    LayoutPlan,
    LayoutResult,
    MutationResult,
    SelectionPosition,
)
from hwp_live_structure_contract import (
    TableCellUpdate,
    TableImageResult,
    TableImageUpdate,
    TableUpdateResult,
)
from hwp_live_template_repeat import (
    TableTemplateRepeatPlan,
    TableTemplateRepeatResult,
)
from hwp_live_workflow import (
    FolderImagePlan,
    FolderImageResult,
    TablePropagationPlan,
    TablePropagationResult,
)
from hwp_office_table import OfficeTableSource
from hwp_live_text_patch_contract import (
    TextPatchPlanGuard,
    TextPatchRequest,
    TextPatchResult,
)


class HancomBridgeDocumentMutationMixin(HancomBridgeMutationRuntime):
    __slots__: tuple[str, ...] = ()

    def replace_selection(
        self,
        session_id: str,
        expected_selection: SelectionPosition,
        expected_text: str,
        replacement: str,
    ) -> MutationResult:
        return self._call_mutation(
            lambda: self._bridge_controller().replace_selection(
                session_id,
                expected_selection,
                expected_text,
                replacement,
            ),
            session_id=session_id,
        )

    def patch_text(
        self,
        session_id: str,
        request: TextPatchRequest,
    ) -> TextPatchResult:
        return self._call_mutation(
            lambda: self._bridge_controller().patch_text(session_id, request),
            session_id=session_id,
        )

    def patch_text_batch(
        self,
        session_id: str,
        requests: tuple[TextPatchRequest, ...],
        plan_guard: TextPatchPlanGuard | None = None,
    ) -> TextPatchResult:
        return self._call_mutation(
            lambda: self._bridge_controller().patch_text_batch(
                session_id, requests, plan_guard
            ),
            session_id=session_id,
        )

    def apply_layout(
        self,
        session_id: str,
        plan: LayoutPlan,
        expected_cursor: tuple[int, int, int],
        expected_selected_text: str | None,
    ) -> LayoutResult:
        return self._call_mutation(
            lambda: self._bridge_controller().apply_layout(
                session_id,
                plan,
                expected_cursor,
                expected_selected_text,
            ),
            session_id=session_id,
        )

    def update_table_cells(
        self,
        session_id: str,
        table_ref: str,
        state_token: str,
        updates: tuple[TableCellUpdate, ...],
    ) -> TableUpdateResult:
        return self._call_mutation(
            lambda: self._bridge_controller().update_table_cells(
                session_id,
                table_ref,
                state_token,
                updates,
            ),
            session_id=session_id,
        )

    def insert_table_images(
        self,
        session_id: str,
        table_ref: str,
        state_token: str,
        images: tuple[TableImageUpdate, ...],
    ) -> TableImageResult:
        return self._call_mutation(
            lambda: self._bridge_controller().insert_table_images(
                session_id,
                table_ref,
                state_token,
                images,
            ),
            session_id=session_id,
        )

    def repeat_table_template(
        self,
        session_id: str,
        plan: TableTemplateRepeatPlan,
    ) -> TableTemplateRepeatResult:
        return self._call_mutation(
            lambda: self._bridge_controller().repeat_table_template(session_id, plan),
            session_id=session_id,
        )

    def import_office_table(
        self,
        session_id: str,
        page: int,
        table_ref: str,
        state_token: str,
        source: OfficeTableSource,
        target_start: str,
    ) -> TableUpdateResult:
        return self._call_mutation(
            lambda: self._bridge_controller().import_office_table(
                session_id,
                page,
                table_ref,
                state_token,
                source,
                target_start,
            ),
            session_id=session_id,
        )

    def propagate_table_cells(
        self,
        session_id: str,
        plan: TablePropagationPlan,
    ) -> TablePropagationResult:
        return self._call_mutation(
            lambda: self._bridge_controller().propagate_table_cells(session_id, plan),
            session_id=session_id,
        )

    def insert_folder_images(
        self,
        session_id: str,
        plan: FolderImagePlan,
    ) -> FolderImageResult:
        return self._call_mutation(
            lambda: self._bridge_controller().insert_folder_images(session_id, plan),
            session_id=session_id,
        )
