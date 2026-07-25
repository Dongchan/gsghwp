from __future__ import annotations

from hwp_live_bridge_contract import BridgeSnapshot
from hwp_live_structure_contract import DocumentStructure, StructureTable


def _table_paths(before: StructureTable, after: StructureTable) -> tuple[str, ...]:
    prefix = f"tables[{after.table_ref}]"
    paths: list[str] = []
    if (before.page_start, before.page_end) != (after.page_start, after.page_end):
        paths.append(prefix + ".pages")
    if (before.rows, before.columns, before.merges) != (
        after.rows,
        after.columns,
        after.merges,
    ):
        paths.append(prefix + ".structure")
    if before.caption != after.caption:
        paths.append(prefix + ".caption")
    old_cells = {cell.address: cell for cell in before.cells}
    new_cells = {cell.address: cell for cell in after.cells}
    for address in sorted(old_cells.keys() | new_cells.keys()):
        if old_cells.get(address) != new_cells.get(address):
            paths.append(prefix + f".cells[{address}]")
    return tuple(paths)


def _structure_paths(
    before: DocumentStructure,
    after: DocumentStructure,
) -> tuple[str, ...]:
    paths: list[str] = []
    if before.page_count != after.page_count:
        paths.append("page_count")
    if before.page_text != after.page_text:
        paths.append("page_text")
    if before.paragraphs != after.paragraphs:
        paths.append("paragraphs")
    if before.controls != after.controls:
        paths.append("controls")
    old_tables = {table.table_ref: table for table in before.tables}
    new_tables = {table.table_ref: table for table in after.tables}
    for reference in sorted(old_tables.keys() | new_tables.keys()):
        old = old_tables.get(reference)
        new = new_tables.get(reference)
        if old is None or new is None:
            paths.append(f"tables[{reference}]")
        else:
            paths.extend(_table_paths(old, new))
    return tuple(paths)


def changed_paths(
    before: BridgeSnapshot,
    after: BridgeSnapshot,
) -> tuple[str, ...]:
    paths: list[str] = list(_structure_paths(before.structure, after.structure))
    if before.context.document.modified != after.context.document.modified:
        paths.append("context.document.modified")
    if before.context.current_page != after.context.current_page:
        paths.append("context.current_page")
    if before.context.cursor != after.context.cursor:
        paths.append("context.cursor")
    if before.context.selection != after.context.selection:
        paths.append("context.selection")
    if before.context.active_target != after.context.active_target:
        paths.append("context.active_target")
    if before.context.selected_text != after.context.selected_text:
        paths.append("context.selected_text")
    if before.context.page_text != after.context.page_text:
        paths.append("context.page_text")
    if before.context.character_style != after.context.character_style:
        paths.append("context.character_style")
    if before.context.paragraph_style != after.context.paragraph_style:
        paths.append("context.paragraph_style")
    if before.context.page_setup != after.context.page_setup:
        paths.append("context.page_setup")
    if before.window.dialogs != after.window.dialogs:
        paths.append("window.dialogs")
    if (
        before.window.exists,
        before.window.visible,
        before.window.enabled,
        before.window.foreground,
    ) != (
        after.window.exists,
        after.window.visible,
        after.window.enabled,
        after.window.foreground,
    ):
        paths.append("window.state")
    if (before.window.title, before.window.class_name) != (
        after.window.title,
        after.window.class_name,
    ):
        paths.append("window.identity")
    return ("snapshot",) if len(paths) > 500 else tuple(paths)
