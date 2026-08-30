from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, final

from hwp_native_graph_models import NativeContent, NativeSpooledBytes


_MEMORY_VALUE_BYTES: Final = 256 * 1024


@dataclass(slots=True)
class SpoolDebugCounters:
    instances: int = 0
    temporary_files: int = 0
    appends: int = 0
    appended_bytes: int = 0
    reads: int = 0
    promotions: int = 0
    closes: int = 0


_debug = SpoolDebugCounters()


def reset_spool_debug_counters() -> None:
    global _debug
    _debug = SpoolDebugCounters()


def read_spool_debug_counters() -> SpoolDebugCounters:
    return SpoolDebugCounters(
        instances=_debug.instances,
        temporary_files=_debug.temporary_files,
        appends=_debug.appends,
        appended_bytes=_debug.appended_bytes,
        reads=_debug.reads,
        promotions=_debug.promotions,
        closes=_debug.closes,
    )


class SpoolOwner(Protocol):
    def adopt(self, path: Path) -> None: ...


@final
class FieldSpool:
    __slots__ = (
        "_file",
        "_length",
        "_memory",
        "_path",
        "_root",
        "_transferred",
    )

    def __init__(self, root: Path) -> None:
        self._root = root
        self._path: Path | None = None
        self._file = None
        self._memory: bytearray | None = bytearray()
        self._length = 0
        self._transferred = False
        _debug.instances += 1

    @property
    def length(self) -> int:
        return self._length

    @property
    def is_spooled(self) -> bool:
        return self._path is not None

    def _promote(self) -> None:
        if self._memory is None:
            return
        self._root.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix="hgn1-field-", suffix=".bin", dir=self._root
        )
        self._path = Path(name)
        self._file = os.fdopen(descriptor, "w+b")
        if self._memory:
            _ = self._file.write(self._memory)
        self._memory = None
        _debug.temporary_files += 1
        _debug.promotions += 1

    def append(self, chunk: bytes | memoryview) -> None:
        if self._memory is not None and self._length + len(chunk) > _MEMORY_VALUE_BYTES:
            self._promote()
        if self._memory is not None:
            self._memory.extend(chunk)
        else:
            assert self._file is not None
            _ = self._file.write(chunk)
        self._length += len(chunk)
        _debug.appends += 1
        _debug.appended_bytes += len(chunk)

    def chunks(self, offset: int = 0, length: int | None = None) -> Iterator[bytes]:
        remaining = self._length - offset if length is None else length
        if self._memory is not None:
            at = offset
            while remaining:
                take = min(1024 * 1024, remaining)
                yield bytes(memoryview(self._memory)[at : at + take])
                at += take
                remaining -= take
            return
        assert self._file is not None
        self._file.flush()
        _ = self._file.seek(offset)
        while remaining:
            chunk = self._file.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("HGN1_SPOOL_TRUNCATED")
            remaining -= len(chunk)
            yield chunk

    def read(self, offset: int = 0, length: int | None = None) -> bytes:
        actual = self._length - offset if length is None else length
        _debug.reads += 1
        if self._memory is not None:
            return bytes(memoryview(self._memory)[offset : offset + actual])
        assert self._file is not None
        self._file.flush()
        _ = self._file.seek(offset)
        value = self._file.read(actual)
        if len(value) != actual:
            raise ValueError("HGN1_SPOOL_TRUNCATED")
        return value

    def content(self, offset: int = 0, length: int | None = None) -> NativeContent:
        actual = self._length - offset if length is None else length
        if actual <= _MEMORY_VALUE_BYTES:
            return self.read(offset, actual)
        assert self._path is not None
        digest = hashlib.sha256()
        for chunk in self.chunks(offset, actual):
            digest.update(chunk)
        return NativeSpooledBytes(
            path=self._path,
            offset=offset,
            length=actual,
            sha256=digest.hexdigest(),
        )

    def transfer(self, owner: SpoolOwner) -> None:
        if self._transferred:
            return
        if self._file is None or self._path is None:
            raise ValueError("HGN1_SPOOL_TRANSFER_MEMORY")
        self._file.close()
        owner.adopt(self._path)
        self._transferred = True

    def close(self) -> None:
        if self._file is not None and not self._file.closed:
            self._file.close()
        if not self._transferred and self._path is not None:
            self._path.unlink(missing_ok=True)
        _debug.closes += 1


__all__ = [
    "FieldSpool",
    "SpoolDebugCounters",
    "SpoolOwner",
    "read_spool_debug_counters",
    "reset_spool_debug_counters",
]
