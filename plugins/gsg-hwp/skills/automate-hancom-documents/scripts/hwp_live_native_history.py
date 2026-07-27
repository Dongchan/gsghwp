from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from hwp_errors import HwpLiveError
from hwp_live_native_batch import probe_official_api
from hwp_official_api_evidence import classify_native_evidence, parse_native_response


type HistoryDirection = Literal["undo", "redo"]


# The native AUTOMATION envelope reports a document state before and after the
# invocation. Only these fields describe document content. `list`, `paragraph`
# and `character` are the caret, and `modified` is a dirty flag: both of them
# move when the engine merely drops a selection, which is exactly the event
# that used to be misreported as a restored edit. Comparing them would prove
# nothing about the document, so they are deliberately excluded.
_CONTENT_STATE_FIELDS = ("page_count", "control_count", "control_hash")
_TRUE_VALUES = frozenset({"1", "true"})
_FALSE_VALUES = frozenset({"0", "false"})


@dataclass(frozen=True, slots=True)
class NativeHistoryStep:
    applied: bool
    content_changed: bool
    elapsed_microseconds: int


@dataclass(frozen=True, slots=True)
class NativeHistoryResult:
    direction: HistoryDirection
    steps: int
    elapsed_microseconds: int
    # None keeps the pre-existing three-field construction working and means
    # "every requested step applied". `execute_native_history` always sets it.
    applied_steps: int | None = None
    content_changed: bool = False

    @property
    def applied(self) -> int:
        return self.steps if self.applied_steps is None else self.applied_steps

    @property
    def exhausted(self) -> bool:
        return self.applied < self.steps


def build_native_history_payload(direction: HistoryDirection) -> str:
    method = {"undo": "Undo", "redo": "Redo"}[direction]
    return f"HCV1\nAUTOMATION\tIXHwpDocument\t{method}\tmethod\nARG\tI4\t1\nEND"


def _content_changed(evidence: Mapping[str, object]) -> bool:
    before = evidence.get("before")
    after = evidence.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    before_state = cast(dict[str, object], before)
    after_state = cast(dict[str, object], after)
    return any(
        before_state.get(field) != after_state.get(field)
        for field in _CONTENT_STATE_FIELDS
    )


def _execute_native_history_step(
    window_handle: int,
    direction: HistoryDirection,
) -> NativeHistoryStep:
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
    # The call itself has to be healthy before its return value means anything.
    # This is intentionally separate from the return value: a well-formed call
    # that answers "there was nothing left to undo" is a fact about the history
    # stack, not a transport failure.
    healthy = (
        evidence.get("kind") == "automation"
        and evidence.get("owner") == "IXHwpDocument"
        and evidence.get("name") == method
        and evidence.get("member_kind") == "method"
        and evidence.get("owner_hresult") == 0
        and evidence.get("argument_hresult") == 0
        and evidence.get("invoke_hresult") == 0
        and evidence.get("document_state_valid") is True
        and evidence.get("variant_type") == 11
    )
    if not healthy:
        raise HwpLiveError(f"한컴 {method} 메서드를 정상적으로 호출하지 못했습니다")
    raw_value = str(evidence.get("value")).casefold()
    if raw_value not in _TRUE_VALUES and raw_value not in _FALSE_VALUES:
        raise HwpLiveError(f"한컴 {method} 메서드 반환값을 해석하지 못했습니다")
    applied = raw_value in _TRUE_VALUES
    # Preserve the previous strictness on the success path: a claimed step must
    # still pass the shared native evidence classifier.
    if applied and classify_native_evidence(evidence) != "passed":
        raise HwpLiveError(
            f"한컴 {method} 메서드가 실행 이력 변경을 확인하지 못했습니다"
        )
    elapsed = evidence.get("elapsed_us")
    if isinstance(elapsed, bool) or not isinstance(elapsed, int) or elapsed < 0:
        raise HwpLiveError("한컴 문서 이력 처리 시간이 올바르지 않습니다")
    return NativeHistoryStep(applied, applied and _content_changed(evidence), elapsed)


def execute_native_history(
    window_handle: int,
    direction: HistoryDirection,
    steps: int,
) -> NativeHistoryResult:
    if isinstance(steps, bool) or steps < 1 or steps > 20:
        raise ValueError("history steps must be between 1 and 20")
    applied_steps = 0
    elapsed = 0
    content_changed = False
    for _ in range(steps):
        step = _execute_native_history_step(window_handle, direction)
        elapsed += step.elapsed_microseconds
        if not step.applied:
            # The engine reports an empty history stack. Stop instead of
            # spending further round trips, and report how far we actually got.
            break
        applied_steps += 1
        content_changed = content_changed or step.content_changed
    return NativeHistoryResult(
        direction,
        steps,
        elapsed,
        applied_steps,
        content_changed,
    )
