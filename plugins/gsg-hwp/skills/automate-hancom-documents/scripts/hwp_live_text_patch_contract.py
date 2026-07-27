from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hwp_live_native_action_models import (
    NativeActionResult,
    NativePosition,
    NativeSnapshot,
)
from hwp_live_native_format_inputs import TextFormatSpec


type TextPatchTargetKind = Literal["current", "range", "find", "table_cell"]


@dataclass(frozen=True, slots=True)
class TextPatchTarget:
    kind: TextPatchTargetKind
    start: NativePosition | None = None
    end: NativePosition | None = None
    occurrence: int | None = None
    match_case: bool = False
    table_instance_id: str | None = None
    cell_address: str | None = None


@dataclass(frozen=True, slots=True)
class TextPatchRequest:
    target: TextPatchTarget
    expected_text: str | None
    replacement: str
    formatting: TextFormatSpec | None = None


def text_patch_minimum_protocol(request: TextPatchRequest) -> Literal[11, 12]:
    target = request.target
    if (
        target.kind == "find"
        and target.table_instance_id is not None
        and target.cell_address is not None
    ):
        return 12
    return 11


@dataclass(frozen=True, slots=True)
class TextPatchResult:
    native: NativeActionResult
    before: NativeSnapshot
    after: NativeSnapshot
