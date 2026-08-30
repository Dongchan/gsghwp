from __future__ import annotations

import time
from pathlib import Path
from typing import Final, final


ROLLBACK_RECOVERY_RETENTION_SECONDS: Final = 24 * 60 * 60
_ROLLBACK_SUFFIX: Final = ".rollback"
_METADATA_SUFFIX: Final = ".gsgmeta"
type RecoveryFilePair = tuple[Path, Path]


@final
class RollbackRecoveryRetention:
    """Track last-resort rollback copies after their history entries are evicted."""

    __slots__ = ("_pairs",)

    def __init__(self) -> None:
        self._pairs: dict[Path, Path] = {}

    @staticmethod
    def _exists(path: Path) -> bool:
        try:
            _ = path.stat()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return True

    @staticmethod
    def _remove(path: Path) -> bool:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return False
        return True

    def discover(self, pairs: tuple[RecoveryFilePair, ...]) -> None:
        for rollback, metadata in pairs:
            if self._exists(rollback) or self._exists(metadata):
                self._pairs[rollback] = metadata

    def discover_directory(self, directory: Path) -> None:
        """Find recovery copies even after their history entries disappeared."""
        try:
            entries = tuple(directory.iterdir())
        except OSError:
            return
        pairs: set[RecoveryFilePair] = set()
        for entry in entries:
            if entry.name.endswith(_ROLLBACK_SUFFIX):
                pairs.add(
                    (
                        entry,
                        entry.with_name(entry.name + _METADATA_SUFFIX),
                    )
                )
            elif entry.name.endswith(_ROLLBACK_SUFFIX + _METADATA_SUFFIX):
                rollback = entry.with_name(entry.name[: -len(_METADATA_SUFFIX)])
                pairs.add((rollback, entry))
        self.discover(tuple(pairs))

    def total_bytes(self) -> int:
        total = 0
        for rollback, metadata in self._pairs.items():
            for path in (rollback, metadata):
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def expire(self) -> None:
        threshold = time.time() - ROLLBACK_RECOVERY_RETENTION_SECONDS
        for rollback, metadata in tuple(self._pairs.items()):
            try:
                modified = rollback.stat().st_mtime
            except FileNotFoundError:
                try:
                    modified = metadata.stat().st_mtime
                except FileNotFoundError:
                    del self._pairs[rollback]
                    continue
                except OSError:
                    continue
            except OSError:
                continue
            if modified > threshold:
                continue
            if self._remove(rollback) and self._remove(metadata):
                del self._pairs[rollback]

    def discard(self, pairs: tuple[RecoveryFilePair, ...]) -> None:
        for rollback, metadata in pairs:
            if self._remove(rollback) and self._remove(metadata):
                _ = self._pairs.pop(rollback, None)

    def clear(self) -> None:
        self._pairs.clear()


def should_capture_full_document_checkpoint(_full_name: str) -> bool:
    """Use bounded native history for deletion instead of copying the document.

    A checkpoint keeps two document-sized copies for one edit and may retain a
    third rollback copy after a failed restore. Control deletion already has a
    bounded, reversible native-history path, so deletion does not pay that disk
    and latency cost by default. The caller must disclose this choice.
    """
    return False
