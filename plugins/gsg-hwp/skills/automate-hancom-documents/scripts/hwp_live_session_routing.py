from __future__ import annotations

import time

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import NativePageInspection
from hwp_live_rot import HwpDocumentCandidate
from hwp_live_session_types import RoutingContextReader
from hwp_live_session_workflow import routing_context_summary
from hwp_operation_contract import OperationRoutingContext


def read_operation_routing_context(
    candidate: HwpDocumentCandidate,
    reader: RoutingContextReader,
    page_hint: int | None,
) -> tuple[NativePageInspection, OperationRoutingContext]:
    started = time.perf_counter_ns()
    page = reader(candidate.window_handle, page_hint)
    elapsed_microseconds = (time.perf_counter_ns() - started) // 1_000
    if page is None:
        raise HwpLiveError("한컴 프로토콜 9 네이티브 빠른 구조 조회를 사용할 수 없습니다")
    return page, routing_context_summary(page, elapsed_microseconds)
