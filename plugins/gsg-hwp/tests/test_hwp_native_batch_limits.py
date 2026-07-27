from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from base64 import b64encode
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import final

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_live_native_batch as native_batch  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_action_contract import (  # noqa: E402
    decode_page_inspection_batch,
)
from hwp_live_native_batch_contract import (  # noqa: E402
    NATIVE_BATCH_MAX_OPERATIONS,
    NATIVE_INSPECT_PAGES_MAX,
    NativeBatchRequest,
    NativeTableBatch,
    NativeTextCell,
    encode_batch_request,
)


_DOCUMENT_METADATA = (37, "C:/docs/repeat.hwp", 400)


def _encode(value: str) -> str:
    return b64encode(value.encode("utf-8")).decode("ascii") or " "


def _page_payload(
    page: int,
    *,
    document_id: int,
    full_name: str,
    page_count: int,
) -> str:
    return "\n".join(
        (
            "HPI1",
            f"DOC\t{document_id}\t{_encode(full_name)}",
            f"PAGE\t{page}\t{page_count}\t{_encode(f'page {page}')}",
            (
                f"CELL\t{_encode('table-1')}\t{_encode(f'A{page}')}"
                f"\t{page}\t1\t1\t{_encode(f'cell {page}')}"
            ),
            (
                f"CTRL_ERROR\t{_encode('table-1')}\t{_encode('CELL_READ')}"
                f"\t{_encode(f'warning {page}')}"
            ),
            "END",
        )
    )


def _batch_payload(
    pages: tuple[int, ...],
    metadata: tuple[int, str, int] = _DOCUMENT_METADATA,
) -> str:
    document_id, full_name, page_count = metadata
    items = (
        "ITEM\t"
        + _encode(
            _page_payload(
                page,
                document_id=document_id,
                full_name=full_name,
                page_count=page_count,
            )
        )
        for page in pages
    )
    return "\n".join(("HPM1", *items, "END"))


@final
class _NativeBatchStub:
    def __init__(
        self,
        metadata_by_call: tuple[tuple[int, str, int], ...] = (_DOCUMENT_METADATA,),
    ) -> None:
        self.metadata_by_call = metadata_by_call
        self.inspect_requests: list[tuple[int, ...]] = []
        self.execute_calls = 0

    def InspectPagesV3(self, pages: str) -> str:
        requested = tuple(int(page) for page in pages.split(","))
        call_index = len(self.inspect_requests)
        self.inspect_requests.append(requested)
        metadata = self.metadata_by_call[
            min(call_index, len(self.metadata_by_call) - 1)
        ]
        return _batch_payload(requested, metadata)

    def Execute(self, payload: str) -> str:
        _ = payload
        self.execute_calls += 1
        return "HCB1\tOK\t0\t0\t1"


@contextmanager
def _fake_com_apartment() -> Generator[tuple[object, object, object], None, None]:
    yield object(), object(), object()


def _install_batch(
    monkeypatch: pytest.MonkeyPatch,
    batch: _NativeBatchStub,
) -> None:
    def batch_for_window(*args: object) -> _NativeBatchStub:
        _ = args
        return batch

    monkeypatch.setattr(native_batch, "_com_apartment", _fake_com_apartment)
    monkeypatch.setattr(native_batch, "_batch_for_window", batch_for_window)


def _text_operations(count: int, *, prefix: str = "A") -> tuple[NativeTextCell, ...]:
    return tuple(
        NativeTextCell(
            address=f"{prefix}{index}",
            expected_text=f"before {index}",
            replacement=f"after {index}",
        )
        for index in range(count)
    )


def test_inspect_pages_over_native_limit_chunks_and_preserves_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = tuple(range(1, NATIVE_INSPECT_PAGES_MAX + 2))
    batch = _NativeBatchStub()
    _install_batch(monkeypatch, batch)

    inspected = native_batch.inspect_native_pages(1234, pages)
    expected = decode_page_inspection_batch(_batch_payload(pages))

    assert inspected == expected
    assert batch.inspect_requests == [
        pages[:NATIVE_INSPECT_PAGES_MAX],
        pages[NATIVE_INSPECT_PAGES_MAX:],
    ]
    assert inspected[0].cells
    assert inspected[-1].inspection_errors


def test_inspect_pages_at_or_below_limit_keeps_one_call_and_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = (9, 3, 5)
    batch = _NativeBatchStub()
    _install_batch(monkeypatch, batch)

    inspected = native_batch.inspect_native_pages(1234, pages)

    assert inspected == decode_page_inspection_batch(_batch_payload(pages))
    assert batch.inspect_requests == [pages]


@pytest.mark.parametrize(
    "changed_metadata",
    (
        (38, _DOCUMENT_METADATA[1], _DOCUMENT_METADATA[2]),
        (_DOCUMENT_METADATA[0], "C:/docs/other.hwp", _DOCUMENT_METADATA[2]),
        (_DOCUMENT_METADATA[0], _DOCUMENT_METADATA[1], 401),
    ),
    ids=("document-id", "full-name", "page-count"),
)
def test_inspect_pages_rejects_document_metadata_change_between_chunks(
    monkeypatch: pytest.MonkeyPatch,
    changed_metadata: tuple[int, str, int],
) -> None:
    pages = tuple(range(1, NATIVE_INSPECT_PAGES_MAX + 2))
    batch = _NativeBatchStub((_DOCUMENT_METADATA, changed_metadata))
    _install_batch(monkeypatch, batch)

    with pytest.raises(
        HwpLiveError, match="문서 정보가 청크 사이에서 일치하지 않습니다"
    ):
        _ = native_batch.inspect_native_pages(1234, pages)

    assert batch.inspect_requests == [
        pages[:NATIVE_INSPECT_PAGES_MAX],
        pages[NATIVE_INSPECT_PAGES_MAX:],
    ]


def test_oversized_atomic_batch_is_rejected_without_execute_or_splitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_count = NATIVE_BATCH_MAX_OPERATIONS // 2
    request = NativeBatchRequest(
        document_id=37,
        full_name="C:/docs/repeat.hwp",
        tables=(
            NativeTableBatch("table-1", _text_operations(first_count, prefix="A")),
            NativeTableBatch(
                "table-2",
                _text_operations(
                    NATIVE_BATCH_MAX_OPERATIONS - first_count + 1,
                    prefix="B",
                ),
            ),
        ),
    )
    batch = _NativeBatchStub()
    _install_batch(monkeypatch, batch)

    with pytest.raises(
        HwpLiveError,
        match="작업 제한을 초과.*원자성을 보존.*요청을 분할하지 않습니다",
    ):
        _ = native_batch.execute_native_batch(1234, request)

    assert batch.execute_calls == 0


def test_encoder_allows_exact_native_operation_limit() -> None:
    request = NativeBatchRequest(
        document_id=37,
        full_name="C:/docs/repeat.hwp",
        tables=(
            NativeTableBatch(
                "table-1",
                _text_operations(NATIVE_BATCH_MAX_OPERATIONS),
            ),
        ),
    )

    payload = encode_batch_request(request)

    assert sum(line.startswith("TEXT\t") for line in payload.splitlines()) == (
        NATIVE_BATCH_MAX_OPERATIONS
    )
