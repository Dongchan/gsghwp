from __future__ import annotations

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final, Literal, cast

from hwp_checkpoint_signature import checkpoint_signature_is_complete
from hwp_errors import HwpLiveError
from hwp_live_edit_history import NativeDocumentStructureSnapshot
from hwp_live_native_batch import (
    execute_native_history_step,
    probe_official_api,
)
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
_DOCUMENT_STRUCTURE_PAYLOAD: Final = (
    "HCV1\nAUTOMATION\tIXHwpDocument\tModified\tproperty\nEND"
)
_expected_content_signature: ContextVar[str] = ContextVar(
    "hwp_native_history_expected_content_signature",
    default="",
)


@contextmanager
def native_history_content_precondition(
    expected_content_signature: str,
) -> Generator[None, None, None]:
    if not checkpoint_signature_is_complete(expected_content_signature):
        raise HwpLiveError(
            "한컴 실행 이력의 예상 문서 지문이 완전하지 않아 문서를 바꾸지 "
            + "않았습니다",
            mutation_started=False,
        )
    token = _expected_content_signature.set(expected_content_signature)
    try:
        yield
    finally:
        _expected_content_signature.reset(token)


@dataclass(frozen=True, slots=True)
class NativeHistoryStep:
    applied: bool
    content_changed: bool
    elapsed_microseconds: int
    before_structure: NativeDocumentStructureSnapshot | None = None
    after_structure: NativeDocumentStructureSnapshot | None = None
    before_content_signature: str = ""
    after_content_signature: str = ""


@dataclass(frozen=True, slots=True)
class NativeHistoryResult:
    direction: HistoryDirection
    steps: int
    elapsed_microseconds: int
    # None keeps the pre-existing three-field construction working and means
    # "every requested step applied". `execute_native_history` always sets it.
    applied_steps: int | None = None
    content_changed: bool = False
    before_structure: NativeDocumentStructureSnapshot | None = None
    after_structure: NativeDocumentStructureSnapshot | None = None
    before_content_signature: str = ""
    after_content_signature: str = ""

    @property
    def applied(self) -> int:
        return self.steps if self.applied_steps is None else self.applied_steps

    @property
    def exhausted(self) -> bool:
        return self.applied < self.steps


def build_native_history_payload(direction: HistoryDirection) -> str:
    method = {"undo": "Undo", "redo": "Redo"}[direction]
    return f"HCV1\nAUTOMATION\tIXHwpDocument\t{method}\tmethod\nARG\tI4\t1\nEND"


def _document_structure(
    page_count: int,
    control_count: int,
    control_hash: int,
) -> NativeDocumentStructureSnapshot | None:
    if (
        isinstance(page_count, bool)
        or page_count < 1
        or isinstance(control_count, bool)
        or control_count < 0
        or isinstance(control_hash, bool)
        or control_hash < 0
    ):
        return None
    return NativeDocumentStructureSnapshot(page_count, control_count, control_hash)


def read_native_document_structure(
    window_handle: int,
) -> NativeDocumentStructureSnapshot | None:
    """Read the whole-document control fingerprint without changing history."""
    response = probe_official_api(window_handle, _DOCUMENT_STRUCTURE_PAYLOAD)
    if response is None:
        return None
    try:
        evidence = parse_native_response(response)
    except ValueError:
        return None
    healthy = (
        evidence.get("kind") == "automation"
        and evidence.get("owner") == "IXHwpDocument"
        and evidence.get("name") == "Modified"
        and evidence.get("member_kind") == "property"
        and classify_native_evidence(evidence) == "passed"
    )
    if not healthy:
        return None
    match evidence.get("before"):
        case {
            "page_count": int(before_pages),
            "control_count": int(before_controls),
            "control_hash": int(before_hash),
        }:
            before = _document_structure(
                before_pages,
                before_controls,
                before_hash,
            )
        case _:
            before = None
    match evidence.get("after"):
        case {
            "page_count": int(after_pages),
            "control_count": int(after_controls),
            "control_hash": int(after_hash),
        }:
            after = _document_structure(after_pages, after_controls, after_hash)
        case _:
            after = None
    return before if before is not None and before == after else None


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
    expected_content_signature = _expected_content_signature.get()
    if expected_content_signature:
        checked = execute_native_history_step(
            window_handle,
            direction,
            expected_content_signature,
        )
        if checked is None:
            raise HwpLiveError(
                "한컴 프로토콜 13 원자적 문서 이력 편집기를 사용할 수 없습니다"
            )
        if (
            checked.before_content_signature != expected_content_signature
            or not checkpoint_signature_is_complete(checked.before_content_signature)
        ):
            raise HwpLiveError(
                "한컴 실행 이력 직전 문서 지문이 예상 상태와 다릅니다",
                mutation_started=False,
            )
        if checkpoint_signature_is_complete(checked.after_content_signature):
            _ = _expected_content_signature.set(checked.after_content_signature)
        return NativeHistoryStep(
            applied=checked.applied == 1,
            content_changed=(
                checked.applied == 1
                and checked.before_content_signature != checked.after_content_signature
            ),
            elapsed_microseconds=checked.elapsed_microseconds,
            before_content_signature=checked.before_content_signature,
            after_content_signature=checked.after_content_signature,
        )
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
    match evidence.get("before"):
        case {
            "page_count": int(before_pages),
            "control_count": int(before_controls),
            "control_hash": int(before_hash),
        }:
            before_structure = _document_structure(
                before_pages,
                before_controls,
                before_hash,
            )
        case _:
            before_structure = None
    match evidence.get("after"):
        case {
            "page_count": int(after_pages),
            "control_count": int(after_controls),
            "control_hash": int(after_hash),
        }:
            after_structure = _document_structure(
                after_pages,
                after_controls,
                after_hash,
            )
        case _:
            after_structure = None
    return NativeHistoryStep(
        applied,
        applied and _content_changed(evidence),
        elapsed,
        before_structure,
        after_structure,
    )


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
    before_structure = None
    after_structure = None
    before_content_signature = ""
    after_content_signature = ""
    for _ in range(steps):
        step = _execute_native_history_step(window_handle, direction)
        if before_structure is None:
            before_structure = step.before_structure
        after_structure = step.after_structure
        if not before_content_signature:
            before_content_signature = step.before_content_signature
        after_content_signature = step.after_content_signature
        elapsed += step.elapsed_microseconds
        if not step.applied:
            # The engine reports an empty history stack. Stop instead of
            # spending further round trips, and report how far we actually got.
            break
        applied_steps += 1
        content_changed = content_changed or step.content_changed
    return NativeHistoryResult(
        direction=direction,
        steps=steps,
        elapsed_microseconds=elapsed,
        applied_steps=applied_steps,
        content_changed=content_changed,
        before_structure=before_structure,
        after_structure=after_structure,
        before_content_signature=before_content_signature,
        after_content_signature=after_content_signature,
    )
