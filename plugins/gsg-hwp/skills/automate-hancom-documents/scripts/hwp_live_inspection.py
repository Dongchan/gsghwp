from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from xml.etree import ElementTree

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication, SelectionRange, ShapeValue
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


def _text(values: dict[str, ShapeValue], key: str) -> str:
    value = values.get(key)
    return value if isinstance(value, str) else ""


def _selection(
    raw: SelectionRange,
) -> SelectionPosition:
    selected, start_list, start_para, start_char, end_list, end_para, end_char = raw
    return SelectionPosition(
        selected=selected,
        start_list=start_list or 0,
        start_paragraph=start_para or 0,
        start_character=start_char or 0,
        end_list=end_list or 0,
        end_paragraph=end_para or 0,
        end_character=end_char or 0,
    )


def _active_target(
    hwp: LiveHwpApplication,
    selection: SelectionRange,
    guard: Callable[[], None],
) -> ActiveHwpTarget:
    guard()
    raw_mode = int(hwp.SelectionMode)
    guard()
    base_mode = raw_mode & 0x0F
    mode_names: dict[int, SelectionModeName] = {
        0: "none",
        1: "text",
        2: "column",
        3: "cells",
        4: "control",
    }
    mode = mode_names.get(base_mode, "unknown")
    in_cell = False
    if base_mode != 4:
        in_cell = bool(hwp.is_cell())
        guard()
    cell_address = hwp.get_cell_addr().strip().upper() if in_cell else None
    guard()
    control_type: str | None = None
    control_instance_id: str | None = None
    if base_mode == 4 or in_cell:
        control = hwp.CurSelectedCtrl if base_mode == 4 else hwp.ParentCtrl
        guard()
        control_type = str(control.CtrlID).strip() or None
        guard()
        control_instance_id = str(control.GetCtrlInstID()).strip() or None
        guard()
    strict_selection = bool(raw_mode & 0x10)
    multiple_cells = base_mode == 3 and (
        strict_selection or selection[1:4] != selection[4:7]
    )
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
    )


def inspect_context(
    hwp: LiveHwpApplication,
    document: OpenDocument,
    guard: Callable[[], None],
) -> LiveContext:
    guard()
    cursor = hwp.get_pos()
    guard()
    selected_range = hwp.get_selected_pos()
    guard()
    active_target = _active_target(hwp, selected_range, guard)
    selected_text = ""
    if selected_range[0]:
        selected_text = hwp.get_text_file(
            format="UNICODE",
            option="saveblock:true",
        )
        guard()
        confirmed_range = hwp.get_selected_pos()
        guard()
        if confirmed_range != selected_range:
            raise HwpLiveError("선택 영역을 읽는 동안 한컴 선택 상태가 바뀌었습니다")
    character = hwp.get_charshape_as_dict()
    guard()
    paragraph = hwp.get_parashape_as_dict()
    guard()
    page = hwp.get_pagedef_as_dict("eng")
    guard()
    current_page = hwp.current_page
    guard()
    page_text = hwp.get_page_text(current_page - 1)[:200_000]
    guard()
    return LiveContext(
        document=document,
        current_page=current_page,
        cursor=CursorPosition(
            list_id=cursor[0],
            paragraph=cursor[1],
            character=cursor[2],
        ),
        selection=_selection(selected_range),
        active_target=active_target,
        selected_text=selected_text[:100_000],
        page_text=page_text,
        character_style=CharacterStyle(
            face_name=_text(character, "FaceNameHangul"),
            height_hwpunit=_integer(character, "Height"),
            bold=bool(_integer(character, "Bold")),
            text_color=_integer(character, "TextColor"),
        ),
        paragraph_style=ParagraphStyle(
            align_type=_integer(paragraph, "AlignType"),
            line_spacing=_integer(paragraph, "LineSpacing"),
            left_margin_hwpunit=_integer(paragraph, "LeftMargin"),
            right_margin_hwpunit=_integer(paragraph, "RightMargin"),
            indentation_hwpunit=_integer(paragraph, "Indentation"),
            previous_spacing_hwpunit=_integer(paragraph, "PrevSpacing"),
            next_spacing_hwpunit=_integer(paragraph, "NextSpacing"),
        ),
        page_setup=PageSetup(
            paper_width_mm=_number(page, "PaperWidth"),
            paper_height_mm=_number(page, "PaperHeight"),
            landscape=_integer(page, "Landscape"),
            top_margin_mm=_number(page, "TopMargin"),
            bottom_margin_mm=_number(page, "BottomMargin"),
            left_margin_mm=_number(page, "LeftMargin"),
            right_margin_mm=_number(page, "RightMargin"),
            header_mm=_number(page, "HeaderLen"),
            footer_mm=_number(page, "FooterLen"),
            gutter_mm=_number(page, "GutterLen"),
            gutter_type=_integer(page, "GutterType"),
        ),
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
        raise HwpLiveError("한컴 문서 스타일 메모리 응답을 해석하지 못했습니다") from error
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
            raise HwpLiveError("한컴 문서 스타일 ID 형식이 올바르지 않습니다") from error
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
