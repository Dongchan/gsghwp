from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from hwp_official_api_contract import OfficialApiCatalog


_CATALOG_PATH = (
    Path(__file__).parents[1]
    / "resources"
    / "hancom_official_api_catalog_v1.json"
)


@lru_cache(maxsize=1)
def load_official_api_catalog() -> OfficialApiCatalog:
    return OfficialApiCatalog.model_validate_json(
        _CATALOG_PATH.read_text(encoding="utf-8")
    )
