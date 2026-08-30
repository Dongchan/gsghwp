from __future__ import annotations

from typing import Literal

from hwp_live_structure_contract import StructureTable
from hwp_operation_contract import HwpOperateTarget, OperationResult
from hwp_operation_registry import operation_registry
from hwp_public_table_plan import (
    PublicTableStructureReader,
    ResolvedPublicTable,
)
from hwp_visibility_series_contract import VisibilitySeriesPlanError
from hwp_visibility_source import extract_visibility_records
from hwp_visibility_template import (
    analyze_visibility_template,
    visibility_template_slots,
)
from hwp_visibility_template_observation import (
    VisibilityTemplateMismatch,
    VisibilityTemplateObservation,
)


type VisibilityTableRole = Literal["source", "template"]
_NEARBY_PAGE_RADIUS = 3


def _ambiguous_observation(
    observation: VisibilityTemplateObservation,
) -> VisibilityTemplateObservation:
    mismatch = VisibilityTemplateMismatch(
        code="ambiguous_multiple_header_structures",
        reason="가시권 분석표 양식 후보가 둘 이상입니다",
        expected="exactly one recognized template table",
        observed=observation.table_ref,
    )
    return observation.model_copy(
        update={
            "condition_unambiguous": False,
            "recognized": False,
            "mismatch_codes": (*observation.mismatch_codes, mismatch.code),
            "mismatches": (*observation.mismatches, mismatch),
        }
    )


def _matches(table: StructureTable, role: VisibilityTableRole) -> bool:
    try:
        if role == "source":
            _ = extract_visibility_records(table)
        else:
            _ = visibility_template_slots(table)
    except VisibilitySeriesPlanError:
        return False
    return True


async def resolve_visibility_table(
    reader: PublicTableStructureReader,
    document_path: str | None,
    page: int,
    role: VisibilityTableRole,
) -> ResolvedPublicTable | OperationResult:
    first = await reader.read_table_structure(document_path, page)
    structures = [first]
    exact_matches = tuple(
        (index, table)
        for index, table in enumerate(first.tables, start=1)
        if _matches(table, role)
    )
    if not exact_matches and not (role == "template" and first.tables):
        for distance in range(1, _NEARBY_PAGE_RADIUS + 1):
            for candidate_page in (page + distance, page - distance):
                if candidate_page < 1 or candidate_page > first.page_count:
                    continue
                candidate = await reader.read_table_structure(
                    document_path,
                    candidate_page,
                )
                structures.append(candidate)
                matches = tuple(
                    (index, table)
                    for index, table in enumerate(candidate.tables, start=1)
                    if _matches(table, role)
                )
                if matches:
                    exact_matches = matches
                    first = candidate
                    break
            if exact_matches:
                break
    if len(exact_matches) != 1:
        role_name = "예비조망점 선정표" if role == "source" else "가시권 분석표 양식"
        inspected_pages = ", ".join(str(item.page) for item in structures)
        observations: tuple[VisibilityTemplateObservation, ...] = ()
        if role == "template":
            if exact_matches:
                observations = tuple(
                    _ambiguous_observation(analyze_visibility_template(table))
                    for _index, table in exact_matches
                )
            else:
                observations = tuple(
                    analyze_visibility_template(table)
                    for structure in structures
                    for table in structure.tables
                )
        return OperationResult(
            status="ambiguous" if exact_matches else "not_found",
            query="예비조망점표 기준 가시권 분석표 동기화",
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            required_inputs=(f"{role}_page",),
            message=(
                f"{first.page}쪽에서 {role_name} 구조가 여러 개입니다"
                if exact_matches
                else f"확인한 {inspected_pages}쪽에서 {role_name} 구조를 찾지 못했습니다"
            ),
            retry_safe=True,
            visibility_template_observation=observations,
        )
    table_index, table = exact_matches[0]
    control_id = table.control_instance_id
    if control_id is None:
        return OperationResult(
            status="known_failure",
            query="예비조망점표 기준 가시권 분석표 동기화",
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            failure_stage="target_resolve",
            message=f"{first.page}쪽 대상 표의 네이티브 식별자를 읽지 못했습니다",
            retry_safe=False,
        )
    return ResolvedPublicTable(
        document_path=document_path or first.full_name,
        structure=first,
        table=table,
        table_index=table_index,
        target=HwpOperateTarget(
            kind="table",
            binding="active",
            page_hint=table.page_start,
            table_index=table_index,
            match_policy="return_candidates",
            control_instance_id=control_id,
        ),
    )
