from __future__ import annotations

from typing import Final

from hwp_official_api_requests import OfficialApiNativeRequest


DISABLED_OFFICIAL_API_CASE_IDS: Final[frozenset[str]] = frozenset(
    (
        "action:0067:CharShapeTextColorGreen",
        "action:0068:CharShapeTextColorRed",
        "action:0365:MakeIndex",
        "action:0608:SaveHistoryItem",
    )
)


def enabled_official_api_requests(
    requests: tuple[OfficialApiNativeRequest, ...],
) -> tuple[OfficialApiNativeRequest, ...]:
    return tuple(
        request
        for request in requests
        if request.case_id not in DISABLED_OFFICIAL_API_CASE_IDS
    )
