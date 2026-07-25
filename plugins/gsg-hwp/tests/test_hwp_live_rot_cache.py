from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_rot import HwpRotCatalog  # noqa: E402
from hwp_live_session_core import LiveHwpSessionCore  # noqa: E402


def test_rot_catalog_reuses_short_lived_scan_cache() -> None:
    catalog = HwpRotCatalog(cache_ttl_seconds=1)
    setattr(catalog, "_cached_candidates", ())
    setattr(catalog, "_cache_deadline", float("inf"))
    with patch.object(
        HwpRotCatalog,
        "_loaded_runtime",
        side_effect=AssertionError("ROT should not be scanned"),
    ):
        assert catalog.scan() == ()
    catalog.close()
    assert getattr(catalog, "_cached_candidates") is None


def test_explicit_document_listing_forces_fresh_rot_scan() -> None:
    catalog = HwpRotCatalog()
    with patch.object(HwpRotCatalog, "scan", return_value=()) as scan:
        core = LiveHwpSessionCore(catalog=catalog)
        try:
            assert core.list_open_documents().documents == ()
            scan.assert_called_once_with(force=True)
        finally:
            core.close()
