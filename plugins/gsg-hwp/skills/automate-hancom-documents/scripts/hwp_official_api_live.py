from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Literal

from pydantic import Field

from hwp_errors import HwpLiveError
from hwp_live_native_batch import probe_official_api
from hwp_live_values import ContractModel
from hwp_official_api_catalog import load_official_api_catalog
from hwp_official_api_evidence import classify_native_evidence, parse_native_response
from hwp_official_api_policy import enabled_official_api_requests
from hwp_official_api_requests import (
    OfficialApiNativeRequest,
    build_official_api_requests,
)


OfficialApiLiveCategory = Literal["action", "parameter_set", "automation"]
OfficialApiLiveStatus = Literal["passed", "failed", "unavailable", "transport_error"]


class OfficialApiLiveItem(ContractModel):
    ordinal: int = Field(ge=1)
    case_id: str
    category: OfficialApiLiveCategory
    name: str
    owner: str | None
    member_kind: str | None
    source_page: int = Field(ge=1)
    input_lines: tuple[str, ...]
    status: OfficialApiLiveStatus
    wall_ms: float = Field(ge=0)
    native_elapsed_us: int | None = Field(default=None, ge=0)
    native_response: str | None
    evidence_json: str | None
    error: str | None


class OfficialApiLiveBatchResult(ContractModel):
    category: OfficialApiLiveCategory
    start: int = Field(ge=1)
    requested: int = Field(ge=1)
    executed: int = Field(ge=0)
    total_in_category: int = Field(ge=1)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    transport_errors: int = Field(ge=0)
    wall_ms: float = Field(ge=0)
    items: tuple[OfficialApiLiveItem, ...]


@lru_cache(maxsize=1)
def _requests() -> tuple[OfficialApiNativeRequest, ...]:
    return enabled_official_api_requests(
        build_official_api_requests(load_official_api_catalog())
    )


def run_official_api_live_batch(
    window_handle: int,
    category: OfficialApiLiveCategory,
    start: int,
    limit: int,
) -> OfficialApiLiveBatchResult:
    if start < 1:
        raise HwpLiveError("공식 API 시작 순번은 1 이상이어야 합니다")
    if limit < 1 or limit > 100:
        raise HwpLiveError("공식 API 배치 크기는 1~100이어야 합니다")

    category_requests = tuple(
        request for request in _requests() if request.category == category
    )
    if start > len(category_requests):
        raise HwpLiveError("공식 API 시작 순번이 해당 분류 범위를 벗어났습니다")

    selected = category_requests[start - 1 : start - 1 + limit]
    batch_started = time.perf_counter()
    items: list[OfficialApiLiveItem] = []
    for offset, request in enumerate(selected):
        item_started = time.perf_counter()
        response: str | None = None
        evidence_json: str | None = None
        native_elapsed_us: int | None = None
        error: str | None = None
        status: OfficialApiLiveStatus
        try:
            response = probe_official_api(window_handle, request.payload)
            if response is None:
                status = "unavailable"
                error = "C++/ATL ProbeOfficialApi를 사용할 수 없습니다"
            else:
                evidence = parse_native_response(response)
                evidence_json = json.dumps(
                    evidence,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                elapsed = evidence.get("elapsed_us")
                native_elapsed_us = elapsed if isinstance(elapsed, int) else None
                status = (
                    "passed"
                    if classify_native_evidence(evidence) == "passed"
                    else "failed"
                )
        except HwpLiveError as caught:
            status = "transport_error"
            error = str(caught)
        except ValueError as caught:
            status = "failed"
            error = str(caught)

        items.append(
            OfficialApiLiveItem(
                ordinal=start + offset,
                case_id=request.case_id,
                category=category,
                name=request.name,
                owner=request.owner,
                member_kind=request.member_kind,
                source_page=request.source_page,
                input_lines=request.input_lines,
                status=status,
                wall_ms=(time.perf_counter() - item_started) * 1_000,
                native_elapsed_us=native_elapsed_us,
                native_response=response,
                evidence_json=evidence_json,
                error=error,
            )
        )

    return OfficialApiLiveBatchResult(
        category=category,
        start=start,
        requested=limit,
        executed=len(items),
        total_in_category=len(category_requests),
        passed=sum(item.status == "passed" for item in items),
        failed=sum(item.status == "failed" for item in items),
        unavailable=sum(item.status == "unavailable" for item in items),
        transport_errors=sum(item.status == "transport_error" for item in items),
        wall_ms=(time.perf_counter() - batch_started) * 1_000,
        items=tuple(items),
    )
