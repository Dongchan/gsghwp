from __future__ import annotations

from collections.abc import Callable
from typing import Final, TypeVar

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


#: 확정에 실패한 문서에 추가 수정을 거절할 때 내는 문장. 왜 막혔는지와 어떻게
#: 풀 수 있는지를 한 문장 안에 담는다 — 이유만 말하면 호출자는 같은 요청을 한
#: 번 더 보내고, 그 재시도는 확정되지 않은 앞선 변경 위에 쌓인다.
_UNCONFIRMED_WRITE_REFUSAL: Final = (
    "이 문서는 직전 변경의 결과를 확정하지 못해 추가 수정을 차단했습니다"
    "; 문서를 다시 관측(hwp_inspect·hwp_inspect_structure)해 현재 상태를 확인하고"
    ", 필요하면 hwp_undo 로 되돌리거나 hwp_reload·재연결로 세션을 새로 연 뒤에"
    " 다시 요청하세요"
)


def require_writable_document(
    unsafe_selectors: set[str],
    selector: str,
    *,
    reobserve: Callable[[], bool] | None = None,
) -> None:
    """Refuse the next write to a document whose last write was never confirmed.

    ``run_layout_mutation`` marks a selector when a mutation raised after it may
    already have touched the document. Writing again on top of that unknown
    state is how one unverified edit becomes several, so the mark stands until
    something proves the document can be observed again: ``reobserve`` returning
    True here, a confirmed mutation clearing it in ``run_layout_mutation``, or
    the session being dropped.
    """
    if selector not in unsafe_selectors:
        return
    if reobserve is not None:
        try:
            recovered = bool(reobserve())
        except Exception:
            recovered = False
        if recovered:
            unsafe_selectors.discard(selector)
            return
    raise HwpLiveError(
        _UNCONFIRMED_WRITE_REFUSAL,
        mutation_started=False,
        safe_to_repeat=True,
    )


def mark_document_unsafe(unsafe_selectors: set[str], selector: str) -> None:
    unsafe_selectors.add(selector)


def run_layout_mutation(
    unsafe_selectors: set[str],
    selector: str,
    operation: Callable[[], T],
) -> T:
    try:
        result = operation()
    except LIVE_OPERATION_ERRORS:
        mark_document_unsafe(unsafe_selectors, selector)
        raise
    # 확정된 쓰기는 표식을 반드시 지운다. 표식을 세우는 곳과 지우는 곳이 같은
    # 함수라 "언제 풀리는가"가 한 자리에서 읽히고, 앞선 실패로 세워진 표식이
    # 뒤에 성공한 쓰기 뒤에도 남아 문서를 영구히 잠그는 일이 생기지 않는다.
    unsafe_selectors.discard(selector)
    return result
