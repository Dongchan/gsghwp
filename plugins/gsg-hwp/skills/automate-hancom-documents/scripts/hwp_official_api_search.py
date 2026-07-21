from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_official_api_contract import (
    OfficialApiCatalog,
    OfficialApiCategory,
    OfficialApiMatch,
    OfficialApiSearchResult,
)


def _score(query: str, name: str, searchable: str) -> int | None:
    normalized_name = name.casefold()
    if normalized_name == query:
        return 0
    if normalized_name.startswith(query):
        return 1
    if query in normalized_name:
        return 2
    if query in searchable.casefold():
        return 3
    return None


def search_official_api(
    catalog: OfficialApiCatalog,
    query: str,
    *,
    category: OfficialApiCategory,
    limit: int,
) -> OfficialApiSearchResult:
    normalized = query.strip().casefold()
    if not normalized:
        raise HwpLiveError("공식 한컴 API 검색어가 비어 있습니다")
    if limit < 1 or limit > 100:
        raise HwpLiveError("공식 한컴 API 검색 결과 수는 1~100이어야 합니다")

    ranked: list[tuple[int, int, str, OfficialApiMatch]] = []
    if category in ("all", "action"):
        for item in catalog.actions:
            score = _score(
                normalized,
                item.name,
                " ".join(
                    filter(None, (item.parameter_set, item.description, item.remarks))
                ),
            )
            if score is not None:
                ranked.append(
                    (
                        score,
                        0,
                        item.name.casefold(),
                        OfficialApiMatch(
                            category="action",
                            name=item.name,
                            source_document="ActionTable_2504.pdf",
                            source_page_start=item.source_page,
                            source_page_end=item.source_page,
                            description=item.description,
                            parameter_set=item.parameter_set,
                        ),
                    )
                )
    if category in ("all", "parameter_set"):
        for item in catalog.parameter_sets:
            item_text = " ".join(
                f"{value.name} {value.value_type} {value.subtype or ''} {value.description}"
                for value in item.items
            )
            score = _score(
                normalized,
                item.name,
                f"{item.description} {item_text}",
            )
            if score is not None:
                ranked.append(
                    (
                        score,
                        1,
                        item.name.casefold(),
                        OfficialApiMatch(
                            category="parameter_set",
                            name=item.name,
                            source_document="ParameterSetTable_2504.pdf",
                            source_page_start=item.source_page_start,
                            source_page_end=item.source_page_end,
                            description=item.description,
                        ),
                    )
                )
    if category in ("all", "automation"):
        for item in catalog.automation_members:
            score = _score(
                normalized,
                item.name,
                f"{item.description} {item.declaration} {item.details}",
            )
            if score is not None:
                ranked.append(
                    (
                        score,
                        2,
                        item.name.casefold(),
                        OfficialApiMatch(
                            category="automation",
                            name=item.name,
                            source_document="HwpAutomation_2504.pdf",
                            source_page_start=item.source_page_start,
                            source_page_end=item.source_page_end,
                            description=item.description,
                            declaration=item.declaration or None,
                            owner=item.owner,
                        ),
                    )
                )

    ranked.sort(key=lambda value: value[:3])
    return OfficialApiSearchResult(
        query=query.strip(),
        category=category,
        total_matches=len(ranked),
        matches=tuple(value[3] for value in ranked[:limit]),
    )
