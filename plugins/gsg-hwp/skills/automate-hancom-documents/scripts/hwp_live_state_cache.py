from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import final

from hwp_live_bridge_contract import BridgeSnapshot, BridgeState
from hwp_live_structure_contract import (
    DocumentStructure,
    StructureTable,
)


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    revision: int
    previous_revision: int
    changed_paths: tuple[str, ...]
    snapshot: BridgeSnapshot


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


@final
class HancomStateCache:
    __slots__ = ("_entries", "_lock", "_revision")

    _entries: dict[int, _CacheEntry]
    _lock: Lock
    _revision: int

    def __init__(self) -> None:
        self._entries = {}
        self._lock = Lock()
        self._revision = 0

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._revision = 0

    def refresh(
        self,
        snapshot: BridgeSnapshot,
        after_revision: int,
    ) -> BridgeState:
        with self._lock:
            previous = self._entries.get(snapshot.structure.page)
            if previous is not None and previous.snapshot == snapshot:
                if after_revision > previous.revision:
                    self._revision = max(self._revision, after_revision) + 1
                    rebased = _CacheEntry(
                        revision=self._revision,
                        previous_revision=after_revision,
                        changed_paths=("snapshot",),
                        snapshot=snapshot,
                    )
                    self._entries[snapshot.structure.page] = rebased
                    return BridgeState(
                        revision=rebased.revision,
                        previous_revision=rebased.previous_revision,
                        full_snapshot=True,
                        changed_paths=("snapshot",),
                        snapshot=snapshot,
                    )
                full = (
                    after_revision == 0
                    or after_revision not in {previous.revision, previous.previous_revision}
                )
                paths = () if after_revision == previous.revision else previous.changed_paths
                return BridgeState(
                    revision=previous.revision,
                    previous_revision=previous.previous_revision,
                    full_snapshot=full,
                    changed_paths=("snapshot",) if full else paths,
                    snapshot=snapshot,
                )
            self._revision = max(self._revision, after_revision) + 1
            prior_revision = previous.revision if previous is not None else 0
            paths = (
                changed_paths(previous.snapshot, snapshot)
                if previous is not None
                else ("snapshot",)
            )
            entry = _CacheEntry(
                revision=self._revision,
                previous_revision=prior_revision,
                changed_paths=paths,
                snapshot=snapshot,
            )
            self._entries[snapshot.structure.page] = entry
            full = (
                previous is None
                or after_revision != prior_revision
                or entry.changed_paths == ("snapshot",)
            )
            return BridgeState(
                revision=entry.revision,
                previous_revision=entry.previous_revision,
                full_snapshot=full,
                changed_paths=("snapshot",) if full else entry.changed_paths,
                snapshot=snapshot,
            )
