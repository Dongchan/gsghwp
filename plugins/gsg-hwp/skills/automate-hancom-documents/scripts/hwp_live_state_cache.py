from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Final, final

from hwp_live_bridge_contract import BridgeSnapshot, BridgeState
from hwp_live_state_diff import changed_paths


_STATE_CACHE_ENTRY_LIMIT: Final = 8
_STATE_CACHE_BYTE_LIMIT: Final = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    revision: int
    previous_revision: int
    changed_paths: tuple[str, ...]
    snapshot: BridgeSnapshot
    size_bytes: int


@final
class HancomStateCache:
    __slots__ = (
        "_entries",
        "_lock",
        "_max_bytes",
        "_max_entries",
        "_revision",
        "_total_bytes",
    )

    _entries: OrderedDict[int, _CacheEntry]
    _lock: Lock
    _max_bytes: int
    _max_entries: int
    _revision: int
    _total_bytes: int

    def __init__(
        self,
        *,
        max_entries: int = _STATE_CACHE_ENTRY_LIMIT,
        max_bytes: int = _STATE_CACHE_BYTE_LIMIT,
    ) -> None:
        self._entries = OrderedDict()
        self._lock = Lock()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._revision = 0
        self._total_bytes = 0

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._revision = 0
            self._total_bytes = 0

    def _retain(self, page: int, entry: _CacheEntry) -> None:
        previous = self._entries.pop(page, None)
        if previous is not None:
            self._total_bytes -= previous.size_bytes
        self._entries[page] = entry
        self._total_bytes += entry.size_bytes
        while (
            len(self._entries) > self._max_entries
            or self._total_bytes > self._max_bytes
        ):
            _, evicted = self._entries.popitem(last=False)
            self._total_bytes -= evicted.size_bytes

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
                        size_bytes=previous.size_bytes,
                    )
                    self._retain(snapshot.structure.page, rebased)
                    return BridgeState(
                        revision=rebased.revision,
                        previous_revision=rebased.previous_revision,
                        full_snapshot=True,
                        changed_paths=("snapshot",),
                        snapshot=snapshot,
                    )
                self._entries.move_to_end(snapshot.structure.page)
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
                size_bytes=len(snapshot.model_dump_json().encode("utf-8")),
            )
            self._retain(snapshot.structure.page, entry)
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
