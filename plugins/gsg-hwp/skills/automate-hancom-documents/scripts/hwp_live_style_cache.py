from __future__ import annotations

import ntpath
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import final

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


@final
class StyleInspectionCache:
    __slots__ = ("_hits", "_key", "_lock", "_misses", "_styles")

    def __init__(self) -> None:
        self._hits = 0
        self._key: _StyleCacheKey | None = None
        self._lock = Lock()
        self._misses = 0
        self._styles: DocumentStyleList | None = None

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
            if self._key == key and self._styles is not None:
                self._hits += 1
                return self._styles
            styles = scan()
            self._key = key
            self._styles = styles
            self._misses += 1
            return styles

    def clear(self) -> None:
        with self._lock:
            self._key = None
            self._styles = None

    @property
    def metrics(self) -> StyleCacheMetrics:
        with self._lock:
            return StyleCacheMetrics(hits=self._hits, misses=self._misses)
