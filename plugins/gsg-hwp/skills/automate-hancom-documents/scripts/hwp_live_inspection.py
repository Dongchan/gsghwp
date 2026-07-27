from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from xml.etree import ElementTree

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication, ShapeValue
from hwp_live_native_action_contract import decoded_logical_cell_selection
from hwp_live_contract import (
    ActiveHwpTarget,
    ActiveTargetKind,
    CharacterStyle,
    CursorPosition,
    DocumentStyle,
    DocumentStyleList,
    LiveContext,
    OpenDocument,
    PageSetup,
    ParagraphStyle,
    SelectionPosition,
    SelectionModeName,
)
from hwp_live_native_action_models import NativeSelection, NativeSnapshot
from hwp_live_safety import LIVE_OPERATION_ERRORS


_style_inspection_lock = Lock()


def _number(values: dict[str, ShapeValue], key: str) -> float:
    value = values.get(key)
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _integer(values: dict[str, ShapeValue], key: str) -> int:
    return int(_number(values, key))


def _selection(
    raw: NativeSelection,
) -> SelectionPosition:
    return SelectionPosition(
        selected=raw.selected,
        start_list=raw.start.list_id,
        start_paragraph=raw.start.paragraph,
        start_character=raw.start.character,
        end_list=raw.end.list_id,
        end_paragraph=raw.end.paragraph,
        end_character=raw.end.character,
    )


def _active_target(
    snapshot: NativeSnapshot,
) -> ActiveHwpTarget:
    selection = snapshot.selection
    raw_mode = selection.mode
    base_mode = raw_mode & 0x0F
    mode_names: dict[int, SelectionModeName] = {
        0: "none",
        1: "text",
        2: "column",
        3: "cells",
        4: "control",
    }
    mode = mode_names.get(base_mode, "unknown")
    native_cell_address = snapshot.cell_address.strip().upper()
    in_cell = base_mode != 4 and bool(native_cell_address)
    cell_address = native_cell_address if in_cell else None
    control_type: str | None = None
    control_instance_id: str | None = None
    if base_mode == 4 or in_cell:
        control_type = snapshot.control_type.strip() or None
        control_instance_id = snapshot.control_instance_id.strip() or None
    strict_selection = bool(raw_mode & 0x10)
    multiple_cells = base_mode == 3 and (
        strict_selection or selection.start != selection.end
    )
    logical_selection = decoded_logical_cell_selection(selection)
    if logical_selection is None:
        selected_cell_addresses = selection.cell_addresses
        selected_cell_address_error = selection.cell_address_error
    else:
        selected_cell_addresses, selected_cell_address_error = logical_selection
    kind: ActiveTargetKind
    if base_mode == 4:
        kind = "selected_table" if control_type == "tbl" else "selected_control"
    elif base_mode == 3:
        kind = "selected_cells"
    elif base_mode == 2:
        kind = "column_selection"
    elif base_mode == 1:
        kind = "selected_text"
    elif base_mode == 0 and in_cell:
        kind = "table_cell"
    elif base_mode == 0:
        kind = "caret"
    else:
        kind = "unknown"
    return ActiveHwpTarget(
        kind=kind,
        selection_mode_raw=raw_mode,
        selection_mode=mode,
        strict_selection=strict_selection,
        multiple_cells=multiple_cells,
        control_type=control_type,
        control_instance_id=control_instance_id,
        cell_address=cell_address,
        cell_addresses=(
            tuple(
                dict.fromkeys(
                    address.strip().upper()
                    for address in selected_cell_addresses
                    if address.strip()
                )
            )
            if base_mode == 3
            else ()
        ),
        cell_address_error=(
            selected_cell_address_error.strip() or None
            if base_mode == 3
            else None
        ),
    )


def inspect_native_context(
    snapshot: NativeSnapshot,
    document: OpenDocument,
    page_text: str,
    page_setup: dict[str, ShapeValue],
) -> LiveContext:
    character = snapshot.character_format
    paragraph = snapshot.paragraph_format
    return LiveContext(
        document=document,
        current_page=snapshot.current_page,
        cursor=CursorPosition(
            list_id=snapshot.cursor.list_id,
            paragraph=snapshot.cursor.paragraph,
            character=snapshot.cursor.character,
        ),
        selection=_selection(snapshot.selection),
        active_target=_active_target(snapshot),
        selected_text=snapshot.selected_text[:100_000],
        page_text=page_text[:200_000],
        character_style=CharacterStyle(
            face_name=character.face_name,
            height_hwpunit=character.height_hwpunit,
            bold=character.bold,
            text_color=character.text_color,
        ),
        paragraph_style=ParagraphStyle(
            align_type=paragraph.alignment,
            line_spacing=paragraph.line_spacing,
            left_margin_hwpunit=paragraph.left_margin_hwpunit,
            right_margin_hwpunit=paragraph.right_margin_hwpunit,
            indentation_hwpunit=paragraph.indentation_hwpunit,
            previous_spacing_hwpunit=paragraph.previous_spacing_hwpunit,
            next_spacing_hwpunit=paragraph.next_spacing_hwpunit,
        ),
        page_setup=PageSetup(
            paper_width_mm=_number(page_setup, "PaperWidth"),
            paper_height_mm=_number(page_setup, "PaperHeight"),
            landscape=_integer(page_setup, "Landscape"),
            top_margin_mm=_number(page_setup, "TopMargin"),
            bottom_margin_mm=_number(page_setup, "BottomMargin"),
            left_margin_mm=_number(page_setup, "LeftMargin"),
            right_margin_mm=_number(page_setup, "RightMargin"),
            header_mm=_number(page_setup, "HeaderLen"),
            footer_mm=_number(page_setup, "FooterLen"),
            gutter_mm=_number(page_setup, "GutterLen"),
            gutter_type=_integer(page_setup, "GutterType"),
        ),
    )


def with_selected_cell_addresses(
    context: LiveContext,
    addresses: tuple[str, ...],
    error: str = "",
) -> LiveContext:
    if context.active_target.kind != "selected_cells":
        return context
    normalized = tuple(
        dict.fromkeys(
            address.strip().upper() for address in addresses if address.strip()
        )
    )
    return context.model_copy(
        update={
            "active_target": context.active_target.model_copy(
                update={
                    "cell_addresses": normalized,
                    "cell_address_error": error.strip() or None,
                }
            )
        }
    )


def inspect_styles(
    hwp: LiveHwpApplication,
    guard: Callable[[], None],
) -> DocumentStyleList:
    guard()
    cursor = hwp.get_pos()
    guard()
    selection = hwp.get_selected_pos()
    guard()
    modified = hwp.IsModified
    guard()
    style_xml = ""
    try:
        with _style_inspection_lock:
            selected = selection[0]
            if not selected:
                selected = hwp.MoveSelRight()
                guard()
                if not selected:
                    selected = hwp.MoveSelLeft()
                    guard()
            style_xml = hwp.get_text_file(
                format="HWPML2X",
                option="saveblock:true" if selected else "",
            )
            guard()
    finally:
        guard()
        try:
            restored = (
                hwp.select_text(selection) if selection[0] else hwp.set_pos(*cursor)
            )
        except LIVE_OPERATION_ERRORS as error:
            raise HwpLiveError(
                "한컴 문서 스타일을 읽은 뒤 커서와 선택 영역을 복원하지 못했습니다"
            ) from error
        if not restored:
            raise HwpLiveError(
                "한컴 문서 스타일을 읽은 뒤 커서와 선택 영역을 복원하지 못했습니다"
            )
        guard()
    restored_cursor = hwp.get_pos()
    guard()
    restored_selection = hwp.get_selected_pos()
    guard()
    current_modified = hwp.IsModified
    guard()
    if (
        restored_selection != selection
        or (not selection[0] and restored_cursor != cursor)
        or current_modified != modified
    ):
        raise HwpLiveError("한컴 문서 스타일을 읽는 동안 문서 상태가 바뀌었습니다")
    if not style_xml:
        raise HwpLiveError("한컴 문서 스타일 메모리 응답이 비어 있습니다")
    try:
        style_elements = ElementTree.fromstring(style_xml).findall(".//STYLE")
    except ElementTree.ParseError as error:
        raise HwpLiveError(
            "한컴 문서 스타일 메모리 응답을 해석하지 못했습니다"
        ) from error
    if not style_elements:
        raise HwpLiveError("한컴 문서에서 스타일 목록을 찾지 못했습니다")
    styles: list[DocumentStyle] = []
    for raw in style_elements:
        raw_style_id = raw.get("Id")
        name = raw.get("Name")
        english_name = raw.get("EngName")
        try:
            style_id = int(raw_style_id) if raw_style_id is not None else -1
        except ValueError as error:
            raise HwpLiveError(
                "한컴 문서 스타일 ID 형식이 올바르지 않습니다"
            ) from error
        if style_id < 0 or not isinstance(name, str) or not name:
            raise HwpLiveError("한컴 문서 스타일 정보 형식이 올바르지 않습니다")
        styles.append(
            DocumentStyle(
                style_id=style_id,
                name=name,
                english_name=english_name if isinstance(english_name, str) else None,
            )
        )
    return DocumentStyleList(styles=tuple(styles))
