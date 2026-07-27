from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast, final
from unittest.mock import patch

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_api import HwpComApplication, HwpComDocument  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate, HwpRotCatalog  # noqa: E402
from hwp_live_session_core import LiveHwpSessionCore  # noqa: E402


@final
class _Document:
    def __init__(self, documents: _Documents, document_id: int, path: str) -> None:
        self._documents = documents
        self.DocumentID = document_id
        self.FullName = path
        self.Format = "HWP"
        self.EditMode = 1
        self.Modified = 0
        self.activation_count = 0

    def SetActive_XHwpDocument(self) -> None:
        self.activation_count += 1
        self._documents.active = self


@final
class _Documents:
    def __init__(self) -> None:
        self.active: _Document

    @property
    def Active_XHwpDocument(self) -> _Document:
        return self.active


@final
class _Application:
    def __init__(self) -> None:
        self.XHwpDocuments = _Documents()
        self.page_counts = {1: 3, 2: 7}

    @property
    def PageCount(self) -> int:
        return self.page_counts[self.XHwpDocuments.active.DocumentID]


def _candidate(
    application: _Application,
    document: _Document,
    *,
    active: bool,
    page_count: int | None,
) -> HwpDocumentCandidate:
    return HwpDocumentCandidate(
        selector=f"document-{document.DocumentID}",
        moniker_name="!HwpObject.test",
        application=cast(HwpComApplication, cast(object, application)),
        document=cast(HwpComDocument, cast(object, document)),
        document_id=document.DocumentID,
        full_name=document.FullName,
        document_format=document.Format,
        edit_mode=document.EditMode,
        window_handle=1234,
        active=active,
        page_count=page_count,
    )


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


def test_document_listing_uses_short_lived_rot_cache() -> None:
    catalog = HwpRotCatalog()
    with patch.object(HwpRotCatalog, "scan", return_value=()) as scan:
        core = LiveHwpSessionCore(catalog=catalog)
        try:
            assert core.list_open_documents().documents == ()
            scan.assert_called_once_with()
        finally:
            core.close()


def test_rot_cache_never_reuses_com_candidates_across_sta_threads() -> None:
    catalog = HwpRotCatalog(cache_ttl_seconds=60)
    setattr(catalog, "_cached_candidates", ())
    setattr(catalog, "_cache_deadline", float("inf"))

    with (
        patch.object(
            HwpRotCatalog,
            "_loaded_runtime",
            side_effect=RuntimeError("fresh thread scan required"),
        ),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        future = executor.submit(catalog.scan)
        with pytest.raises(RuntimeError, match="fresh thread scan required"):
            _ = future.result()

    catalog.close()


def test_rot_catalog_does_not_activate_inactive_documents_for_page_count() -> None:
    application = _Application()
    first = _Document(application.XHwpDocuments, 1, r"C:\docs\first.hwp")
    second = _Document(application.XHwpDocuments, 2, r"C:\docs\second.hwp")
    application.XHwpDocuments.active = first
    catalog = HwpRotCatalog()

    resolved = catalog._resolve_page_counts(
        (
            _candidate(application, first, active=True, page_count=3),
            _candidate(application, second, active=False, page_count=None),
        )
    )

    assert tuple(candidate.page_count for candidate in resolved) == (3, None)
    assert tuple(candidate.public().page_count for candidate in resolved) == (3, 0)
    assert application.XHwpDocuments.active is first
    assert second.activation_count == 0
    assert first.activation_count == 0

    resolved_again = catalog._resolve_page_counts(
        (
            _candidate(application, first, active=True, page_count=3),
            _candidate(application, second, active=False, page_count=None),
        )
    )

    assert tuple(candidate.page_count for candidate in resolved_again) == (3, None)
    assert second.activation_count == 0
    assert first.activation_count == 0
    catalog.close()
