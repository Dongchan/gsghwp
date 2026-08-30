from __future__ import annotations

import ntpath
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Final, final

from hwp_live_contract import DocumentStyleList


@dataclass(frozen=True, slots=True)
class StyleCacheMetrics:
    hits: int
    misses: int


@dataclass(frozen=True, slots=True)
class _StyleCacheKey:
    document_id: int
    full_name: str
    state_token: str


_STYLE_CACHE_ENTRY_LIMIT: Final = 8


@final
class StyleInspectionCache:
    __slots__ = ("_entries", "_hits", "_lock", "_max_entries", "_misses")

    _entries: OrderedDict[_StyleCacheKey, DocumentStyleList]
    _hits: int
    _lock: Lock
    _max_entries: int
    _misses: int

    def __init__(self, *, max_entries: int = _STYLE_CACHE_ENTRY_LIMIT) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._entries = OrderedDict()
        self._hits = 0
        self._lock = Lock()
        self._max_entries = max_entries
        self._misses = 0

    @staticmethod
    def _key_for(
        document_id: int,
        full_name: str,
        state_token: str,
    ) -> _StyleCacheKey:
        return _StyleCacheKey(
            document_id=document_id,
            full_name=ntpath.normcase(ntpath.normpath(full_name)),
            state_token=state_token,
        )

    def resolve(
        self,
        document_id: int,
        full_name: str,
        state_token: str,
        scan: Callable[[], DocumentStyleList],
    ) -> DocumentStyleList:
        key = self._key_for(document_id, full_name, state_token)
        with self._lock:
            styles = self._entries.get(key)
            if styles is not None:
                self._entries.move_to_end(key)
                self._hits += 1
                return styles
            styles = scan()
            self._entries[key] = styles
            self._misses += 1
            while len(self._entries) > self._max_entries:
                _ = self._entries.popitem(last=False)
            return styles

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    @property
    def metrics(self) -> StyleCacheMetrics:
        with self._lock:
            return StyleCacheMetrics(hits=self._hits, misses=self._misses)
