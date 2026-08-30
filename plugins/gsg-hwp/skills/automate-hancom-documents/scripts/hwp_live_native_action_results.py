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


# Codes that report what the bridge could not *read about* a control, not that
# the control itself failed inspection. The CTRL and CELL records for such a
# control are complete and trustworthy, so a reader must keep the control and
# only treat the named observation as missing.
#
# CELL_FORMAT is the appearance sample of a table. The table's cells, sizes and
# text all arrived; only the fill/border/margin/font readback for the sampled
# cells did not. Dropping the table over that would lose real data.
_READ_ONLY_INSPECTION_ERROR_CODES = frozenset({"CELL_FORMAT"})


def is_structural_inspection_error(code: str) -> bool:
    """Whether this error means the control's own records cannot be trusted."""
    return code not in _READ_ONLY_INSPECTION_ERROR_CODES


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
class NativeCellBorder:
    """One side of one cell, exactly as the bridge reported it.

    ``None`` means the bridge did not report the value -- an older bridge, or a
    property this HWP build does not expose. It never means "no border": a
    ``line_type`` of 0 is HWP's own "None" and is a real observation.
    """

    line_type: int | None = None
    width: int | None = None
    color: int | None = None


@dataclass(frozen=True, slots=True)
class NativeCellFormat:
    """How some sampled cells of a table actually look.

    Sampled, not summarised: the bridge reports a few cells per table picked by
    position alone. ``addresses`` lists every sampled cell that reported these
    exact values, so cells only share a record because the document gave them
    the same numbers. Nothing here says which row is a header -- that judgement
    belongs to the reader, not to the wire.
    """

    table_instance_id: str
    addresses: tuple[str, ...]
    fill_color: int | None = None
    fill_brush: int | None = None
    border_left: NativeCellBorder = NativeCellBorder()
    border_right: NativeCellBorder = NativeCellBorder()
    border_top: NativeCellBorder = NativeCellBorder()
    border_bottom: NativeCellBorder = NativeCellBorder()
    margin_left_hwpunit: int | None = None
    margin_right_hwpunit: int | None = None
    margin_top_hwpunit: int | None = None
    margin_bottom_hwpunit: int | None = None
    vertical_align: int | None = None
    alignment: int | None = None
    face_name: str | None = None
    character_height: int | None = None
    bold: bool | None = None


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
    cell_formats: tuple[NativeCellFormat, ...] = ()


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
class NativeDetailedParagraph:
    position: NativePosition
    page_start: int
    page_end: int
    text_available: bool
    text: str
    style_id: int | None
    face_name: str | None
    height_hwpunit: int | None
    bold: bool | None
    text_color: int | None
    alignment: int | None
    line_spacing: int | None
    left_margin_hwpunit: int | None
    right_margin_hwpunit: int | None
    indentation_hwpunit: int | None
    previous_spacing_hwpunit: int | None
    next_spacing_hwpunit: int | None
    heading_type: int | None
    heading_level: int | None


@dataclass(frozen=True, slots=True)
class NativeUnsupportedRecord:
    record_type: str
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class NativeParagraphStyle:
    """One body paragraph as the open document actually stores it.

    ``lead_text`` is only the first few characters: enough to tell ``○``,
    ``(1)``, ``제1장`` apart, small enough that a document with thousands of
    paragraphs still fits one response.
    """

    paragraph: int
    style_id: int | None
    lead_text: str
    face_name: str | None = None
    height: int | None = None
    bold: bool | None = None
    # Everything below is the paragraph's *own* shape, read at detail level 2.
    # ``None`` means the bridge did not report it (older bridge, level 1, or a
    # property this HWP build does not expose) -- never "the document set 0".
    text_color: int | None = None
    alignment: int | None = None
    line_spacing: int | None = None
    left_margin_hwpunit: int | None = None
    right_margin_hwpunit: int | None = None
    indentation_hwpunit: int | None = None
    previous_spacing_hwpunit: int | None = None
    next_spacing_hwpunit: int | None = None
    heading_type: int | None = None
    heading_level: int | None = None
    # Detail level 3 adds evidence for convention inference. Tail text is only
    # populated when the native scan reached the real paragraph end.
    tail_text: str = ""
    text_length: int | None = None
    text_complete: bool = False
    face_name_latin: str | None = None
    face_name_hanja: str | None = None
    face_name_japanese: str | None = None
    face_name_other: str | None = None
    face_name_symbol: str | None = None
    face_name_user: str | None = None


@dataclass(frozen=True, slots=True)
class NativeParagraphStyleScan:
    document_id: int
    full_name: str
    list_id: int
    start: int
    complete: bool
    paragraphs: tuple[NativeParagraphStyle, ...]


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
    paragraphs: tuple[NativeDetailedParagraph, ...] = ()
    paragraphs_complete: bool = False
    paragraph_scan_error: str | None = None
    unsupported_records: tuple[NativeUnsupportedRecord, ...] = ()
    inspection_errors: tuple[NativeControlInspectionError, ...] = ()
    cell_formats: tuple[NativeCellFormat, ...] = ()
