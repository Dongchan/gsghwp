from __future__ import annotations

from typing import Literal

from pydantic import Field

from hwp_live_values import ContractModel
from hwp_official_api_contract import OfficialApiCatalog, OfficialMemberKind


RuntimeApiCategory = Literal["action", "parameter_set", "automation"]
RuntimeExecutionPath = Literal["native_in_process"]
RuntimeStatus = Literal[
    "not_run",
    "passed",
    "failed",
    "state_required",
    "unavailable",
    "external_dependency",
    "process_exit",
]


class OfficialApiRuntimeCase(ContractModel):
    case_id: str = Field(pattern=r"^(?:action|parameter_set|automation):\d{4}:.+$")
    category: RuntimeApiCategory
    ordinal: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    owner: str | None = Field(default=None, max_length=100)
    member_kind: OfficialMemberKind | None = None
    source_page: int = Field(ge=1)
    execution_path: RuntimeExecutionPath = "native_in_process"
    status: RuntimeStatus = "not_run"


def build_runtime_cases(
    catalog: OfficialApiCatalog,
) -> tuple[OfficialApiRuntimeCase, ...]:
    actions = tuple(
        OfficialApiRuntimeCase(
            case_id=f"action:{index:04d}:{item.name}",
            category="action",
            ordinal=index,
            name=item.name,
            source_page=item.source_page,
        )
        for index, item in enumerate(catalog.actions, 1)
    )
    parameter_sets = tuple(
        OfficialApiRuntimeCase(
            case_id=f"parameter_set:{index:04d}:{item.name}",
            category="parameter_set",
            ordinal=index,
            name=item.name,
            source_page=item.source_page_start,
        )
        for index, item in enumerate(catalog.parameter_sets, 1)
    )
    automation = tuple(
        OfficialApiRuntimeCase(
            case_id=f"automation:{index:04d}:{item.owner}.{item.name}",
            category="automation",
            ordinal=index,
            name=item.name,
            owner=item.owner,
            member_kind=item.member_kind,
            source_page=item.source_page_start,
        )
        for index, item in enumerate(catalog.automation_members, 1)
    )
    return (*actions, *parameter_sets, *automation)
