from __future__ import annotations

from collections import Counter

from hwp_official_api_contract import (
    OfficialApiCatalog,
    OfficialApiCatalogTotals,
    OfficialApiCoverageReport,
    OfficialApiNativeCoverage,
)


_SCALAR_PARAMETER_TYPES = frozenset(
    {
        "PIT_BSTR",
        "PIT_I",
        "PIT_I1",
        "PIT_I4",
        "PIT_U",
        "PIT_UI",
        "PIT_UI1",
        "PIT_UI2",
        "PIT_UI4",
        "PMT_BOOL",
        "PMT_INT",
        "PMT_UINT",
        "PMT_UINT32",
    }
)
_NESTED_PARAMETER_TYPES = frozenset({"PIT_SET"})
_ARRAY_PARAMETER_TYPES = frozenset({"PIT_ARRAY"})
_UNSUPPORTED_PARAMETER_TYPES = frozenset({"PIT_BINDATA", "PIT_BSRT"})


def _native_plain_name(value: str) -> bool:
    return 0 < len(value) <= 128 and all(
        character.isalnum() or character in "_." for character in value
    )


def build_official_api_coverage(
    catalog: OfficialApiCatalog,
) -> OfficialApiCoverageReport:
    native_names = (
        *(item.name for item in catalog.actions),
        *(item.name for item in catalog.parameter_sets),
        *(
            item.name
            for item in catalog.automation_members
            if item.member_kind == "method"
        ),
    )
    invalid_names = sorted({name for name in native_names if not _native_plain_name(name)})
    if invalid_names:
        names = ", ".join(invalid_names)
        raise ValueError(f"official names rejected by the native protocol: {names}")

    parameter_sets = {item.name for item in catalog.parameter_sets}
    parameterized_actions = tuple(
        item for item in catalog.actions if item.parameter_set is not None
    )
    unresolved_actions = tuple(
        item
        for item in parameterized_actions
        if item.parameter_set not in parameter_sets
    )
    parameter_types = Counter(
        item.value_type
        for parameter_set in catalog.parameter_sets
        for item in parameter_set.items
    )
    classified_types = (
        _SCALAR_PARAMETER_TYPES
        | _NESTED_PARAMETER_TYPES
        | _ARRAY_PARAMETER_TYPES
        | _UNSUPPORTED_PARAMETER_TYPES
    )
    unclassified = set(parameter_types) - classified_types
    if unclassified:
        names = ", ".join(sorted(unclassified))
        raise ValueError(f"unclassified official parameter types: {names}")

    member_counts = Counter(item.member_kind for item in catalog.automation_members)
    return OfficialApiCoverageReport(
        schema_version=1,
        catalog=OfficialApiCatalogTotals(
            source_documents=len(catalog.sources),
            action_entries=len(catalog.actions),
            unique_actions=len({item.name for item in catalog.actions}),
            parameter_set_entries=len(catalog.parameter_sets),
            unique_parameter_sets=len(parameter_sets),
            parameter_items=sum(len(item.items) for item in catalog.parameter_sets),
            automation_entries=len(catalog.automation_members),
            unique_automation_members=len(
                {
                    (item.owner, item.name, item.member_kind)
                    for item in catalog.automation_members
                }
            ),
            automation_methods=member_counts["method"],
            automation_properties=member_counts["property"],
            automation_events=member_counts["event"],
        ),
        native_generic=OfficialApiNativeCoverage(
            run_action_names=len(catalog.actions),
            parameterized_actions=len(parameterized_actions),
            exact_parameter_set_mappings=(
                len(parameterized_actions) - len(unresolved_actions)
            ),
            unresolved_parameter_set_mappings=len(unresolved_actions),
            unresolved_parameter_set_tokens=tuple(
                sorted(
                    {
                        item.parameter_set
                        for item in unresolved_actions
                        if item.parameter_set is not None
                    }
                )
            ),
            scalar_parameter_items=sum(
                count
                for value_type, count in parameter_types.items()
                if value_type in _SCALAR_PARAMETER_TYPES
            ),
            nested_parameter_items=sum(
                count
                for value_type, count in parameter_types.items()
                if value_type in _NESTED_PARAMETER_TYPES
            ),
            array_parameter_items=sum(
                count
                for value_type, count in parameter_types.items()
                if value_type in _ARRAY_PARAMETER_TYPES
            ),
            unsupported_parameter_items=sum(
                count
                for value_type, count in parameter_types.items()
                if value_type in _UNSUPPORTED_PARAMETER_TYPES
            ),
            unsupported_parameter_types=tuple(
                sorted(set(parameter_types) & _UNSUPPORTED_PARAMETER_TYPES)
            ),
            automation_method_names_dispatchable=member_counts["method"],
            automation_properties_supported=0,
            automation_events_supported=0,
        ),
        limitations=(
            "Catalog counts prove native protocol encoding, not CreateAction/CreateSet/Execute success in every HWP version or document context.",
            "RUN accepts every indexed Action name, but the live HWP version can reject an unavailable name.",
            "ACTION is mechanically mapped only when the PDF names an exact ParameterSet identifier.",
            "PIT_BINDATA and the PDF token PIT_BSRT are not represented by the generic value encoder.",
            "CALL accepts method names and scalar input values; return, by-reference, dispatch, property, and event surfaces are catalog-only.",
        ),
    )
