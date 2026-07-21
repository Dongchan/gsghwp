from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hwp_errors import HwpLiveError
from hwp_live_native_batch import probe_official_api
from hwp_official_api_evidence import classify_native_evidence, parse_native_response


type HistoryDirection = Literal["undo", "redo"]


@dataclass(frozen=True, slots=True)
class NativeHistoryResult:
    direction: HistoryDirection
    steps: int
    elapsed_microseconds: int


def build_native_history_payload(direction: HistoryDirection) -> str:
    method = {"undo": "Undo", "redo": "Redo"}[direction]
    return (
        "HCV1\n"
        f"AUTOMATION\tIXHwpDocument\t{method}\tmethod\n"
        "ARG\tI4\t1\n"
        "END"
    )


def _execute_native_history_step(
    window_handle: int,
    direction: HistoryDirection,
) -> int:
    response = probe_official_api(
        window_handle,
        build_native_history_payload(direction),
    )
    if response is None:
        raise HwpLiveError("한컴 프로토콜 9 문서 이력 편집기를 사용할 수 없습니다")
    try:
        evidence = parse_native_response(response)
    except ValueError as error:
        raise HwpLiveError("한컴 문서 이력 응답 형식이 올바르지 않습니다") from error
    method = "Undo" if direction == "undo" else "Redo"
    valid = (
        evidence.get("kind") == "automation"
        and evidence.get("owner") == "IXHwpDocument"
        and evidence.get("name") == method
        and classify_native_evidence(evidence) == "passed"
        and evidence.get("variant_type") == 11
        and evidence.get("value") == "1"
    )
    if not valid:
        raise HwpLiveError(f"한컴 {method} 메서드가 실행 이력 변경을 확인하지 못했습니다")
    elapsed = evidence.get("elapsed_us")
    if isinstance(elapsed, bool) or not isinstance(elapsed, int) or elapsed < 0:
        raise HwpLiveError("한컴 문서 이력 처리 시간이 올바르지 않습니다")
    return elapsed


def execute_native_history(
    window_handle: int,
    direction: HistoryDirection,
    steps: int,
) -> NativeHistoryResult:
    if isinstance(steps, bool) or steps < 1 or steps > 20:
        raise ValueError("history steps must be between 1 and 20")
    elapsed = sum(
        _execute_native_history_step(window_handle, direction)
        for _ in range(steps)
    )
    return NativeHistoryResult(direction, steps, elapsed)
