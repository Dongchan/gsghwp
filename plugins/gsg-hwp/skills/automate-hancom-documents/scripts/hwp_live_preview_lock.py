from __future__ import annotations

import msvcrt
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, final


def _open_lock_stream(path: Path) -> BinaryIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b", buffering=0)
    if stream.seek(0, 2) == 0:
        _ = stream.write(b"\0")
    _ = stream.seek(0)
    return stream


@final
class PreviewFileLock:
    __slots__ = ("_active", "_stream")

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._active = True

    @classmethod
    def acquire(cls, path: Path) -> PreviewFileLock:
        stream = _open_lock_stream(path)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        except OSError:
            stream.close()
            raise
        return cls(stream)

    @classmethod
    def try_acquire(cls, path: Path) -> PreviewFileLock | None:
        stream = _open_lock_stream(path)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            stream.close()
            return None
        return cls(stream)

    def close(self) -> None:
        if not self._active:
            return
        self._active = False
        try:
            _ = self._stream.seek(0)
            msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._stream.close()

    def __enter__(self) -> PreviewFileLock:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc_value, traceback)
        self.close()
