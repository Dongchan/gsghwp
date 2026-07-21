from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_native_batch import native_batch_available
from hwp_live_session_edit import LiveHwpEditSession
from hwp_live_session_structure import (
    PendingTableImages,
    PendingTableUpdate,
    insert_validated_table_image_batch,
    update_validated_table_batch,
)
from hwp_live_structure_contract import (
    DocumentStructure,
    TableImageUpdate,
    TableUpdateResult,
)
from hwp_live_template_repeat import (
    TableTemplateRepeatPlan,
    TableTemplateRepeatResult,
    repeat_table_template,
)
from hwp_live_workflow import (
    FolderImagePlan,
    FolderImageResult,
    InsertedImageTableResult,
    PropagatedTableResult,
    TablePropagationPlan,
    TablePropagationResult,
    image_for_keys,
    transfer_updates,
)
from hwp_office_table import (
    OfficeTableSource,
    read_office_table,
    table_updates_from_matrix,
)


class LiveHwpDataSession(LiveHwpEditSession):
    __slots__: tuple[str, ...] = ()
    _structure_snapshot: DocumentStructure | None

    def import_office_table(
        self,
        session_id: str,
        page: int,
        table_ref: str,
        state_token: str,
        source: OfficeTableSource,
        target_start: str,
    ) -> TableUpdateResult:
        matrix = read_office_table(source)
        snapshot = self.structure(session_id, page)
        if snapshot.state_token != state_token:
            raise HwpLiveError(
                "한컴 문서 구조가 조회 이후 바뀌어 Office 표 가져오기를 중단했습니다"
            )
        updates = table_updates_from_matrix(
            self._table(snapshot, table_ref),
            matrix,
            target_start=target_start,
        )
        return self.update_table_cells(session_id, table_ref, state_token, updates)

    def propagate_table_cells(
        self,
        session_id: str,
        plan: TablePropagationPlan,
    ) -> TablePropagationResult:
        source_snapshot = self.structure(session_id, plan.source_page)
        source = self._table(source_snapshot, plan.source_table_ref)
        candidate, hwp = self._validate(session_id)
        if not native_batch_available(candidate.window_handle):
            raise HwpLiveError(
                "한컴 네이티브 배치 DLL 적용 후 한/글을 한 번 다시 실행해야 합니다"
            )
        references = tuple(target.table_ref for target in plan.targets)
        if len(references) != len(set(references)):
            raise HwpLiveError(
                "한 번의 연쇄 채움에서 같은 대상 표를 두 번 지정할 수 없습니다"
            )
        pending: list[PendingTableUpdate] = []
        compact_results: list[PropagatedTableResult] = []
        for target in plan.targets:
            snapshot = self.structure(session_id, target.page)
            updates = transfer_updates(
                source,
                self._table(snapshot, target.table_ref),
                target.transfers,
            )
            pending.append(
                PendingTableUpdate(
                    table_ref=target.table_ref,
                    state_token=snapshot.state_token,
                    updates=updates,
                    snapshot=snapshot,
                )
            )
            compact_results.append(
                PropagatedTableResult(
                    page=target.page,
                    table_ref=target.table_ref,
                    updated_addresses=tuple(update.address for update in updates),
                )
            )
        candidate, _ = self._validate(session_id)
        self._structure_snapshot = None
        native = update_validated_table_batch(
            hwp,
            candidate,
            tuple(pending),
            self._unsafe_selectors,
            self._guard(candidate, hwp),
        )
        return TablePropagationResult(
            source_table_ref=plan.source_table_ref,
            target_results=tuple(compact_results),
            transferred_cell_count=sum(len(item.updates) for item in pending),
            native_elapsed_microseconds=native.elapsed_microseconds,
        )

    def repeat_table_template(
        self,
        session_id: str,
        plan: TableTemplateRepeatPlan,
    ) -> TableTemplateRepeatResult:
        candidate, _ = self._validate(session_id)
        self._structure_snapshot = None
        return repeat_table_template(candidate.window_handle, plan)

    def insert_folder_images(
        self,
        session_id: str,
        plan: FolderImagePlan,
    ) -> FolderImageResult:
        resolved = tuple(
            tuple(
                TableImageUpdate(
                    address=cell.address,
                    expected_text=cell.expected_text,
                    path=image_for_keys(plan.folder, cell.match_keys, cell.role_keys),
                )
                for cell in target.cells
            )
            for target in plan.targets
        )
        pending: list[PendingTableImages] = []
        compact_results: list[InsertedImageTableResult] = []
        for target, images in zip(plan.targets, resolved, strict=True):
            snapshot = self.structure(session_id, target.page)
            _ = self._table(snapshot, target.table_ref)
            pending.append(
                PendingTableImages(
                    table_ref=target.table_ref,
                    state_token=snapshot.state_token,
                    images=images,
                    snapshot=snapshot,
                )
            )
            compact_results.append(
                InsertedImageTableResult(
                    page=target.page,
                    table_ref=target.table_ref,
                    inserted_addresses=tuple(image.address for image in images),
                    inserted_paths=tuple(image.path for image in images),
                )
            )
        candidate, hwp = self._validate(session_id)
        self._structure_snapshot = None
        native = insert_validated_table_image_batch(
            hwp,
            candidate,
            tuple(pending),
            self._unsafe_selectors,
            self._guard(candidate, hwp),
        )
        return FolderImageResult(
            target_results=tuple(compact_results),
            inserted_image_count=sum(len(item.images) for item in pending),
            native_elapsed_microseconds=native.elapsed_microseconds,
        )
