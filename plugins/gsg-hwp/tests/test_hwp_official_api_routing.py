from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar, Final
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict


SCRIPTS: Final = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_official_api_catalog import load_official_api_catalog  # noqa: E402
import hwp_official_api_live  # noqa: E402
from hwp_official_api_policy import (  # noqa: E402
    DISABLED_OFFICIAL_API_CASE_IDS,
    enabled_official_api_requests,
)
from hwp_official_api_requests import build_official_api_requests  # noqa: E402
from hwp_official_api_runtime import build_runtime_cases  # noqa: E402


# 전역 위치다. 종류별 번호(case_id 의 0067 등)와 혼동하면 엉뚱한 API 를 막게 된다.
# 실제로 (67, 68, 365, 608) 로 잘못 들어가 CharShapeTextColorGreen/Red·MakeIndex 를
# 막고, 실제로 실패하는 ExportStyle·ImportStyle·Clone 을 열어두고 있었다.
_DISABLED_GLOBAL_POSITIONS: Final = (608, 1143, 1144, 1441)
_DISABLED_CASE_IDS: Final = frozenset(
    (
        "action:0608:SaveHistoryItem",
        "automation:0067:IHwpObject.ExportStyle",
        "automation:0068:IHwpObject.ImportStyle",
        "automation:0365:IDHwpParameterArray.Clone",
    )
)


class _OfficialApiCompatibility(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    official_api_catalog_entries: int
    official_api_enabled_routes: int
    official_api_disabled_case_ids: tuple[str, ...]


def test_mcp_routes_every_official_api_except_four_disabled_cases() -> None:
    catalog = load_official_api_catalog()
    runtime_cases = build_runtime_cases(catalog)
    requests = enabled_official_api_requests(build_official_api_requests(catalog))
    disabled_case_ids = frozenset(
        runtime_cases[position - 1].case_id for position in _DISABLED_GLOBAL_POSITIONS
    )

    assert len(runtime_cases) == 1_452
    assert disabled_case_ids == _DISABLED_CASE_IDS
    assert DISABLED_OFFICIAL_API_CASE_IDS == _DISABLED_CASE_IDS
    assert len(requests) == 1_448
    assert frozenset(request.case_id for request in requests) == (
        frozenset(case.case_id for case in runtime_cases) - disabled_case_ids
    )

    with patch.object(hwp_official_api_live, "probe_official_api", return_value=None):
        batch = hwp_official_api_live.run_official_api_live_batch(
            window_handle=1,
            category="action",
            start=1,
            limit=1,
        )

    # action 933 / automation 373 / parameter_set 142 = 1,448.
    # 종전 930 은 action 4건을 막던 잘못된 목록 기준이었다.
    assert batch.total_in_category == 933


def test_compatibility_manifest_declares_official_api_routing() -> None:
    manifest = _OfficialApiCompatibility.model_validate_json(
        (SCRIPTS.parents[2] / "compatibility-manifest.json").read_text(encoding="utf-8")
    )

    assert manifest.official_api_catalog_entries == 1_452
    assert manifest.official_api_enabled_routes == 1_448
    assert frozenset(manifest.official_api_disabled_case_ids) == _DISABLED_CASE_IDS
