from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pywintypes import com_error

from hwp_errors import HwpLiveError


T = TypeVar("T")


LIVE_OPERATION_ERRORS = (
    HwpLiveError,
    com_error,
    AttributeError,
    AssertionError,
    KeyError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def require_writable_document(unsafe_selectors: set[str], selector: str) -> None:
    if selector in unsafe_selectors:
        raise HwpLiveError(
            "이 문서는 이전 변경 결과를 확정할 수 없어 추가 수정을 차단했습니다"
        )


def mark_document_unsafe(unsafe_selectors: set[str], selector: str) -> None:
    unsafe_selectors.add(selector)


def run_layout_mutation(
    unsafe_selectors: set[str],
    selector: str,
    operation: Callable[[], T],
) -> T:
    try:
        return operation()
    except LIVE_OPERATION_ERRORS:
        mark_document_unsafe(unsafe_selectors, selector)
        raise
