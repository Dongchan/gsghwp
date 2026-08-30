from __future__ import annotations

from typing import Annotated, Protocol, TypedDict, cast, final

from pydantic import Field

from hwp_errors import HwpLiveError
from hwp_live_contract import (
    DocumentStyleList,
    LiveContext,
    PreviewResult,
    StyleReadOutcome,
)
from hwp_live_grounding import HwpGroundingReport, HwpGroundingRequest
from hwp_live_structure_contract import DocumentStructure, FastPageInspection
from hwp_live_values import ContractModel
from hwp_patch_plan_projection import PatchPlanResult, build_patch_plan
from hwp_mcp_wrappers import compact_structured_result
from hwp_object_control_types import is_picture_control_type, is_table_control_type
from hwp_public_action_contract import PublicObjectTargetStore
from hwp_public_table_inventory import (
    DocumentTableFact,
    DocumentTableInventory,
    TableInventoryPageError,
    TableInventoryPageFact,
    TablePictureFact,
)
from hwp_public_table_target import PublicTableTargetStore


class PatchPlanResponse(ContractModel):
    result: PatchPlanResult


class _TableAccumulator(TypedDict):
    page: int
    rows: int | None
    columns: int | None
    pictures: dict[str, str]


class PublicInspectionExecutor(Protocol):
    async def inspect_context(self, document_selector: str | None) -> LiveContext: ...

    async def read_content_revision(self, document_selector: str | None) -> str: ...

    async def inspect_styles(
        self,
        document_selector: str | None,
    ) -> StyleReadOutcome: ...

    async def inspect_page_fast(
        self,
        document_selector: str | None,
        page: int,
        include_cells: bool,
    ) -> FastPageInspection: ...

    async def inspect_structure(
        self,
        document_selector: str | None,
        page: int,
    ) -> DocumentStructure: ...

    async def render_page(
        self,
        document_selector: str | None,
        page: int,
        dpi: int,
    ) -> PreviewResult: ...

    async def ground_document(
        self,
        document_selector: str | None,
        request: HwpGroundingRequest,
    ) -> HwpGroundingReport: ...


@final
class HwpPublicInspectionTools:
    __slots__ = ("_executor", "_object_targets", "_table_targets")

    def __init__(
        self,
        executor: PublicInspectionExecutor,
        object_targets: PublicObjectTargetStore,
        table_targets: PublicTableTargetStore,
    ) -> None:
        self._executor = executor
        self._object_targets = object_targets
        self._table_targets = table_targets

    async def hwp_inspect(
        self,
        *,
        document_path: str | None = None,
    ) -> LiveContext:
        inspection = await self._executor.inspect_context(document_path)
        # 쪽 수가 아직 확정되지 않았으면(막 연 큰 문서) 조회를 거부하지 않고
        # 그대로 돌려주되, 모르는 값을 아는 척하지 않는다.
        page_count = inspection.document.page_count
        total = "미확정" if page_count is None else str(page_count)
        return compact_structured_result(
            inspection,
            summary=f"hwp_inspect ok: page={inspection.current_page}/{total}",
        )

    async def hwp_list_styles(
        self,
        *,
        document_path: str | None = None,
    ) -> DocumentStyleList:
        outcome = await self._executor.inspect_styles(document_path)
        # 목록을 최신이라고 보증하지 못한 읽기도 거부하지 않고 돌려준다. 다만
        # 읽은 값을 검증된 것처럼 주면 안 되므로 요약에 그 사실을 함께 적는다.
        # 구조화 payload 는 DocumentStyleList 그대로여서 공개 스키마는 그대로다.
        unverified = (
            ""
            if outcome.unverified_reason is None
            else f" (최신 보증 안 됨: {outcome.unverified_reason})"
        )
        count = len(outcome.styles.styles)
        # 정의된 스타일 수와 그 중 문서가 실제로 쓰는 스타일 수는 다른 사실이다.
        # 관측하지 못했으면 0 이 아니라 아예 말하지 않는다.
        observed = outcome.styles.observed_usage
        used = (
            ""
            if observed is None
            else f" used={len(observed.styles)}/{observed.counted_paragraphs}문단"
        )
        return compact_structured_result(
            outcome.styles,
            summary=f"hwp_list_styles ok: styles={count}{used}{unverified}",
        )

    async def hwp_ground_document(
        self,
        *,
        pages: Annotated[tuple[int, ...], Field(min_length=1, max_length=2)],
        dpi: Annotated[int, Field(ge=72, le=600)] = 144,
        include_overlay: bool = False,
        document_path: Annotated[str | None, Field(max_length=32_767)] = None,
    ) -> HwpGroundingReport:
        report = await self._executor.ground_document(
            document_path,
            HwpGroundingRequest(
                document_path=document_path,
                pages=pages,
                dpi=dpi,
                include_overlay=include_overlay,
            ),
        )
        return compact_structured_result(
            report,
            summary=(
                f"hwp_ground_document ok: pages={len(report.geometry)} "
                f"clean={len(report.clean_renders)} overlay={len(report.overlays)}"
            ),
        )

    async def hwp_inspect_page_fast(
        self,
        *,
        page: int = 0,
        include_cells: bool = False,
        document_path: str | None = None,
    ) -> FastPageInspection:
        inspection = await self._executor.inspect_page_fast(
            document_path,
            page,
            include_cells,
        )
        self._object_targets.remember_inspection(inspection, document_path)
        self._table_targets.remember_inspection(inspection)
        return compact_structured_result(
            inspection,
            summary=(
                "hwp_inspect_page_fast ok: "
                f"page={inspection.page}/{inspection.page_count} "
                f"controls={len(inspection.controls)} cells={len(inspection.cells)} "
                f"errors={len(inspection.inspection_errors)}"
            ),
        )

    async def hwp_inspect_patch_plan(
        self,
        *,
        pages: Annotated[tuple[int, ...], Field(min_length=1, max_length=32)],
        include_paragraphs: bool = True,
        include_cells: bool = True,
        max_items: Annotated[int, Field(ge=1, le=512)] = 512,
        max_utf8_bytes: Annotated[int, Field(ge=8_192, le=98_304)] = 65_536,
        document_path: Annotated[str | None, Field(max_length=32_767)] = None,
    ) -> PatchPlanResponse:
        if len(set(pages)) != len(pages):
            raise HwpLiveError("patch plan pages는 중복될 수 없습니다")
        revision_before = await self._executor.read_content_revision(document_path)
        structures = tuple(
            [
                await self._executor.inspect_structure(document_path, page)
                for page in sorted(pages)
            ]
        )
        revision_after = await self._executor.read_content_revision(document_path)
        if revision_after != revision_before:
            raise HwpLiveError(
                "patch plan을 읽는 동안 문서 content revision이 바뀌었습니다",
                mutation_started=False,
                safe_to_repeat=True,
            )
        result = build_patch_plan(
            structures,
            include_paragraphs,
            include_cells,
            max_items,
            max_utf8_bytes,
            revision_before,
        )
        return compact_structured_result(
            PatchPlanResponse(result=result),
            summary=(f"hwp_inspect_patch_plan {result.status}: pages={len(pages)}"),
        )

    async def hwp_find_tables(
        self,
        *,
        min_pictures: Annotated[int, Field(ge=0)] = 0,
        document_path: str | None = None,
    ) -> DocumentTableInventory:
        context = await self._executor.inspect_context(document_path)
        inspections: dict[int, FastPageInspection] = {}
        page_count = context.document.page_count
        if page_count is None:
            current = await self._executor.inspect_page_fast(document_path, 0, True)
            inspections[current.page] = current
            page_count = current.page_count

        page_facts: list[TableInventoryPageFact] = []
        table_facts: dict[str, _TableAccumulator] = {}
        for page in range(1, page_count + 1):
            try:
                inspection = inspections.get(page)
                if inspection is None:
                    inspection = await self._executor.inspect_page_fast(
                        document_path, page, True
                    )
                self._object_targets.remember_inspection(inspection, document_path)
                self._table_targets.remember_inspection(inspection)
            except Exception as error:  # noqa: BLE001 - every unread page is returned
                page_facts.append(
                    TableInventoryPageFact(
                        page=page,
                        read=False,
                        table_count=0,
                        picture_count=0,
                        errors=(
                            TableInventoryPageError(
                                code=type(error).__name__, message=str(error)
                            ),
                        ),
                    )
                )
                continue

            page_table_ids = {
                control.instance_id
                for control in inspection.controls
                if is_table_control_type(control.control_type)
            }
            page_table_ids.update(cell.table_instance_id for cell in inspection.cells)
            page_pictures = tuple(
                control
                for control in inspection.controls
                if is_picture_control_type(control.control_type)
                and control.parent_table_instance_id is not None
                and control.parent_cell_address is not None
            )
            for table_id in page_table_ids:
                table_control = next(
                    (
                        control
                        for control in inspection.controls
                        if control.instance_id == table_id
                        and is_table_control_type(control.control_type)
                    ),
                    None,
                )
                _ = table_facts.setdefault(
                    table_id,
                    {
                        "page": page,
                        "rows": None if table_control is None else table_control.rows,
                        "columns": (
                            None if table_control is None else table_control.columns
                        ),
                        "pictures": {},
                    },
                )
                if table_control is not None:
                    table_facts[table_id]["rows"] = table_control.rows
                    table_facts[table_id]["columns"] = table_control.columns
            for picture in page_pictures:
                table_id = cast(str, picture.parent_table_instance_id)
                cell_address = cast(str, picture.parent_cell_address)
                pictures = table_facts[table_id]["pictures"]
                pictures[picture.instance_id] = cell_address

            errors = tuple(
                TableInventoryPageError(
                    code=error.code,
                    message=error.message,
                    control_instance_id=error.control_instance_id,
                )
                for error in inspection.inspection_errors
            )
            page_facts.append(
                TableInventoryPageFact(
                    page=page,
                    read=True,
                    table_count=len(page_table_ids),
                    picture_count=len(page_pictures),
                    errors=errors,
                )
            )

        tables: list[DocumentTableFact] = []
        for table_id, values in table_facts.items():
            picture_values = values["pictures"]
            pictures = tuple(
                TablePictureFact(instance_id=picture_id, cell_address=cell_address)
                for picture_id, cell_address in sorted(picture_values.items())
            )
            if len(pictures) < min_pictures:
                continue
            tables.append(
                DocumentTableFact(
                    page=values["page"],
                    table_instance_id=table_id,
                    rows=values["rows"],
                    columns=values["columns"],
                    picture_count=len(pictures),
                    pictures=pictures,
                )
            )
        tables.sort(key=lambda table: (table.page, table.table_instance_id))
        result = DocumentTableInventory(
            document_id=context.document.document_id,
            full_name=context.document.full_name,
            page_count=page_count,
            min_pictures=min_pictures,
            tables=tuple(tables),
            pages=tuple(page_facts),
        )
        return compact_structured_result(
            result,
            summary=(
                f"hwp_find_tables ok: pages={page_count} tables={len(tables)} "
                f"unread={sum(not page.read for page in page_facts)}"
            ),
        )

    async def hwp_render_page(
        self,
        *,
        page: int = 0,
        dpi: int = 144,
        document_path: str | None = None,
    ) -> PreviewResult:
        preview = await self._executor.render_page(document_path, page, dpi)
        return compact_structured_result(
            preview,
            summary=f"hwp_render_page ok: page={preview.page} path={preview.path}",
        )

    async def hwp_inspect_structure(
        self,
        *,
        page: int = 0,
        document_path: str | None = None,
    ) -> DocumentStructure:
        structure = await self._executor.inspect_structure(document_path, page)
        return compact_structured_result(
            structure,
            summary=(
                "hwp_inspect_structure ok: "
                f"page={structure.page}/{structure.page_count} "
                f"controls={len(structure.controls)} tables={len(structure.tables)}"
            ),
        )
