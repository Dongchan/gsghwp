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
            )
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
            )
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
            )
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
            )
        )

    def repeat_table_template(
        self,
        session_id: str,
        plan: TableTemplateRepeatPlan,
    ) -> TableTemplateRepeatResult:
        return self._call_mutation(
            lambda: self._bridge_controller().repeat_table_template(session_id, plan)
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
            )
        )

    def propagate_table_cells(
        self,
        session_id: str,
        plan: TablePropagationPlan,
    ) -> TablePropagationResult:
        return self._call_mutation(
            lambda: self._bridge_controller().propagate_table_cells(session_id, plan)
        )

    def insert_folder_images(
        self,
        session_id: str,
        plan: FolderImagePlan,
    ) -> FolderImageResult:
        return self._call_mutation(
            lambda: self._bridge_controller().insert_folder_images(session_id, plan)
        )
