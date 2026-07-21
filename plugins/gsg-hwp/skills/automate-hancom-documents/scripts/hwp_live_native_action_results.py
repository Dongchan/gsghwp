from __future__ import annotations

from dataclasses import dataclass

from hwp_live_native_action_commands import (
    NativeCharacterFormat,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
)


@dataclass(frozen=True, slots=True)
class NativeVoidCallResult:
    method: str


@dataclass(frozen=True, slots=True)
class NativeBooleanCallResult:
    method: str
    value: bool


@dataclass(frozen=True, slots=True)
class NativeIntegerCallResult:
    method: str
    value: int


@dataclass(frozen=True, slots=True)
class NativeTextCallResult:
    method: str
    value: str


type NativeCallResult = (
    NativeVoidCallResult
    | NativeBooleanCallResult
    | NativeIntegerCallResult
    | NativeTextCallResult
)


@dataclass(frozen=True, slots=True)
class NativeActionResult:
    commands_executed: int
    actions_executed: int
    text_insertions: int
    image_insertions: int
    elapsed_microseconds: int
    created_control_ids: tuple[str, ...]
    call_results: tuple[NativeCallResult, ...] = ()
    image_timing_count: int = 0
    image_max_microseconds: int = 0
    image_total_microseconds: int = 0


@dataclass(frozen=True, slots=True)
class NativeSnapshot:
    document_id: int
    full_name: str
    current_page: int
    page_count: int
    modified: bool
    cursor: NativePosition
    selection: NativeSelection
    selected_text: str
    control_type: str
    control_instance_id: str
    cell_address: str
    style_id: int
    character_format: NativeCharacterFormat
    paragraph_format: NativeParagraphFormat


@dataclass(frozen=True, slots=True)
class NativePageControl:
    control_type: str
    instance_id: str
    anchor: NativePosition
    rows: int | None
    columns: int | None
    width_hwpunit: int | None = None
    height_hwpunit: int | None = None
    anchor_style_id: int | None = None
    anchor_paragraph_format: NativeParagraphFormat | None = None


@dataclass(frozen=True, slots=True)
class NativeControlInspectionError:
    control_instance_id: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class NativePageCell:
    table_instance_id: str
    address: str
    list_id: int
    row_span: int
    column_span: int
    text: str
    width_hwpunit: int | None = None
    height_hwpunit: int | None = None


@dataclass(frozen=True, slots=True)
class NativePageInspection:
    document_id: int
    full_name: str
    page: int
    page_count: int
    text: str
    controls: tuple[NativePageControl, ...]
    cells: tuple[NativePageCell, ...] = ()
    inspection_errors: tuple[NativeControlInspectionError, ...] = ()


@dataclass(frozen=True, slots=True)
class NativeDetailedControl:
    control_type: str
    instance_id: str
    user_description: str
    anchor: NativePosition
    page_start: int
    page_end: int
    top_level: bool
    rows: int | None
    columns: int | None
    width_hwpunit: int | None = None
    height_hwpunit: int | None = None


@dataclass(frozen=True, slots=True)
class NativeDetailedCell:
    table_instance_id: str
    address: str
    list_id: int
    row_span: int
    column_span: int
    page_start: int
    page_end: int
    text: str
    width_hwpunit: int | None = None
    height_hwpunit: int | None = None


@dataclass(frozen=True, slots=True)
class NativeDetailedCaption:
    table_instance_id: str
    text: str
    automatic_number: bool
    style_id: int | None
    style_name: str | None
    page_start: int
    page_end: int


@dataclass(frozen=True, slots=True)
class NativeDetailedInspection:
    document_id: int
    full_name: str
    page: int
    page_count: int
    text: str
    controls: tuple[NativeDetailedControl, ...]
    cells: tuple[NativeDetailedCell, ...]
    captions: tuple[NativeDetailedCaption, ...]
    inspection_errors: tuple[NativeControlInspectionError, ...] = ()
