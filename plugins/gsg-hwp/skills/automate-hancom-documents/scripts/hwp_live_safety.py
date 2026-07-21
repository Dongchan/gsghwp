from __future__ import annotations

from collections.abc import Callable

from pywintypes import com_error

from hwp_errors import HwpLiveError


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
    _ = (unsafe_selectors, selector)


def run_layout_mutation(
    unsafe_selectors: set[str],
    selector: str,
    operation: Callable[[], None],
) -> None:
    _ = (unsafe_selectors, selector)
    operation()
