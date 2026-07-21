from __future__ import annotations

from typing import Protocol, final

from hwp_live_contract import DocumentStyleList, LiveContext, PreviewResult
from hwp_live_structure_contract import DocumentStructure, FastPageInspection
from hwp_public_action_contract import PublicObjectTargetStore
from hwp_public_table_target import PublicTableTargetStore


class PublicInspectionExecutor(Protocol):
    async def inspect_context(self, document_selector: str | None) -> LiveContext: ...

    async def inspect_styles(
        self,
        document_selector: str | None,
    ) -> DocumentStyleList: ...

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
        return await self._executor.inspect_context(document_path)

    async def hwp_list_styles(
        self,
        *,
        document_path: str | None = None,
    ) -> DocumentStyleList:
        return await self._executor.inspect_styles(document_path)

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
        self._object_targets.remember_inspection(inspection)
        self._table_targets.remember_inspection(inspection)
        return inspection

    async def hwp_render_page(
        self,
        *,
        page: int = 0,
        dpi: int = 144,
        document_path: str | None = None,
    ) -> PreviewResult:
        return await self._executor.render_page(document_path, page, dpi)

    async def hwp_inspect_structure(
        self,
        *,
        page: int = 0,
        document_path: str | None = None,
    ) -> DocumentStructure:
        return await self._executor.inspect_structure(document_path, page)
