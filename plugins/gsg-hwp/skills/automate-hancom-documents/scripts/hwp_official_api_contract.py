from __future__ import annotations

from typing import Literal

from pydantic import Field

from hwp_live_values import ContractModel


OfficialApiCategory = Literal["all", "action", "parameter_set", "automation"]
OfficialMemberKind = Literal["method", "property", "event"]


class OfficialApiSource(ContractModel):
    file_name: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)


class OfficialAction(ContractModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    parameter_set: str | None = Field(default=None, max_length=100)
    description: str = Field(max_length=10_000)
    remarks: str = Field(max_length=10_000)
    source_page: int = Field(ge=1)


class OfficialParameterItem(ContractModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    value_type: str = Field(pattern=r"^(?:PIT|PMT)_[A-Z0-9_]+$")
    subtype: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z][A-Za-z0-9_]*$",
    )
    description: str = Field(max_length=20_000)
    source_page: int = Field(ge=1)


class OfficialParameterSet(ContractModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    description: str = Field(max_length=10_000)
    source_page_start: int = Field(ge=1)
    source_page_end: int = Field(ge=1)
    items: tuple[OfficialParameterItem, ...]


class OfficialAutomationMember(ContractModel):
    owner: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    member_kind: OfficialMemberKind
    description: str = Field(max_length=20_000)
    declaration: str = Field(max_length=20_000)
    details: str = Field(max_length=100_000)
    source_page_start: int = Field(ge=1)
    source_page_end: int = Field(ge=1)


class OfficialApiMatch(ContractModel):
    category: Literal["action", "parameter_set", "automation"]
    name: str = Field(min_length=1, max_length=200)
    source_document: str = Field(min_length=1, max_length=200)
    source_page_start: int = Field(ge=1)
    source_page_end: int = Field(ge=1)
    description: str = Field(max_length=20_000)
    declaration: str | None = Field(default=None, max_length=20_000)
    parameter_set: str | None = Field(default=None, max_length=100)
    owner: str | None = Field(default=None, max_length=100)


class OfficialApiSearchResult(ContractModel):
    query: str = Field(min_length=1, max_length=200)
    category: OfficialApiCategory
    total_matches: int = Field(ge=0)
    matches: tuple[OfficialApiMatch, ...]


class OfficialApiCatalogTotals(ContractModel):
    source_documents: int = Field(ge=1)
    action_entries: int = Field(ge=0)
    unique_actions: int = Field(ge=0)
    parameter_set_entries: int = Field(ge=0)
    unique_parameter_sets: int = Field(ge=0)
    parameter_items: int = Field(ge=0)
    automation_entries: int = Field(ge=0)
    unique_automation_members: int = Field(ge=0)
    automation_methods: int = Field(ge=0)
    automation_properties: int = Field(ge=0)
    automation_events: int = Field(ge=0)


class OfficialApiNativeCoverage(ContractModel):
    run_action_names: int = Field(ge=0)
    parameterized_actions: int = Field(ge=0)
    exact_parameter_set_mappings: int = Field(ge=0)
    unresolved_parameter_set_mappings: int = Field(ge=0)
    unresolved_parameter_set_tokens: tuple[str, ...]
    scalar_parameter_items: int = Field(ge=0)
    nested_parameter_items: int = Field(ge=0)
    array_parameter_items: int = Field(ge=0)
    unsupported_parameter_items: int = Field(ge=0)
    unsupported_parameter_types: tuple[str, ...]
    automation_method_names_dispatchable: int = Field(ge=0)
    automation_properties_supported: int = Field(ge=0)
    automation_events_supported: int = Field(ge=0)


class OfficialApiCoverageReport(ContractModel):
    schema_version: Literal[1]
    catalog: OfficialApiCatalogTotals
    native_generic: OfficialApiNativeCoverage
    limitations: tuple[str, ...]


class OfficialApiCatalog(ContractModel):
    schema_version: Literal[1]
    sources: tuple[OfficialApiSource, ...]
    actions: tuple[OfficialAction, ...]
    parameter_sets: tuple[OfficialParameterSet, ...]
    automation_members: tuple[OfficialAutomationMember, ...]
