from __future__ import annotations

import hashlib
import ntpath
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, final
from uuid import uuid4

from pydantic import StrictInt, ValidationError, field_validator

from hwp_document_convention_contract import (
    ConventionCacheMetadata,
    ConventionCacheMissReason,
    DocumentConventionProfile,
)
from hwp_live_preview_lock import PreviewFileLock
from hwp_live_values import ContractModel
from hwp_operation_journal_contract import default_operation_journal_path

_SCHEMA_VERSION: Final = 2
_MAX_AGE: Final = timedelta(days=7)
_SAMPLE_BYTES: Final = 1024 * 1024
_CHEAP_METHOD: Final = "normalized_path+size+mtime_ns"
_HASH_METHOD: Final = "normalized_path+size+mtime_ns+sha256(first_1mib+last_1mib+size)"


class _FileIdentity(ContractModel):
    normalized_path: str
    size: int
    mtime_ns: int
    partial_sha256: str


class _PersistentEntry(ContractModel):
    schema_version: StrictInt
    created_at: datetime
    identity: _FileIdentity
    profile: DocumentConventionProfile

    @field_validator("schema_version")
    @classmethod
    def require_schema_version_two(cls, value: int) -> int:
        if value != _SCHEMA_VERSION:
            raise ValueError("unsupported convention cache schema")
        return value

    @field_validator("created_at")
    @classmethod
    def normalize_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cache created_at must include a UTC offset")
        return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ConventionCacheLookup:
    profile: DocumentConventionProfile | None
    metadata: ConventionCacheMetadata


def default_convention_cache_root() -> Path:
    return default_operation_journal_path().parent / "convention-cache-v2"


def normalized_document_path(path: Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(path.resolve(strict=False))))


def partial_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        first = stream.read(_SAMPLE_BYTES)
        _ = stream.seek(0, os.SEEK_END)
        size = stream.tell()
        tail_start = max(len(first), size - _SAMPLE_BYTES)
        _ = stream.seek(tail_start)
        last = stream.read(_SAMPLE_BYTES)
    digest.update(size.to_bytes(8, "little", signed=False))
    digest.update(first)
    digest.update(last)
    return digest.hexdigest()


def _metadata(
    *,
    hit: bool,
    method: str,
    created_at: datetime | None = None,
    reason: ConventionCacheMissReason | None = None,
) -> ConventionCacheMetadata:
    return ConventionCacheMetadata(
        hit=hit,
        created_at=created_at,
        freshness_checked_by=method,
        currentness="historical_snapshot" if hit else "live_observation",
        miss_reason=reason,
    )


@final
class ConventionProfileCache:
    __slots__ = (
        "_clock",
        "_deferred_hits",
        "_hash_file",
        "_read_misses",
        "_root",
    )

    def __init__(
        self,
        root: Path | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        hash_file: Callable[[Path], str] = partial_file_sha256,
    ) -> None:
        self._root = (root or default_convention_cache_root()).resolve()
        self._clock = clock
        self._deferred_hits: set[Path] = set()
        self._hash_file = hash_file
        self._read_misses: dict[Path, ConventionCacheMetadata] = {}

    def cache_path(self, document: Path) -> Path:
        key = hashlib.sha256(
            normalized_document_path(document).encode("utf-8")
        ).hexdigest()
        return self._root / f"{key}.json"

    def lock_path(self, document: Path) -> Path:
        return self._root / ".locks" / f"{self.cache_path(document).stem}.lock"

    def _miss(
        self,
        reason: ConventionCacheMissReason,
        method: str,
    ) -> ConventionCacheLookup:
        return ConventionCacheLookup(
            None, _metadata(hit=False, method=method, reason=reason)
        )

    def _path_miss(
        self,
        path: Path,
        reason: ConventionCacheMissReason,
        method: str,
    ) -> ConventionCacheLookup:
        metadata = self._read_misses.setdefault(
            path,
            _metadata(hit=False, method=method, reason=reason),
        )
        return ConventionCacheLookup(None, metadata)

    def lookup(self, document: Path, *, modified: bool) -> ConventionCacheLookup:
        if modified:
            return self._miss("dirty_open_document", "dirty-open-document-bypass")
        path = self.cache_path(document)
        if path in self._deferred_hits:
            return ConventionCacheLookup(None, self._read_misses[path])
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return self._path_miss(path, "missing", "normalized_path")
        except OSError:
            return self._path_miss(path, "invalid_cache", "cache-json-read")
        try:
            entry = _PersistentEntry.model_validate_json(raw, strict=True)
        except (ValidationError, ValueError):
            return self._path_miss(path, "invalid_cache", "cache-json-schema-v1")
        now = self._clock()
        if now - entry.created_at >= _MAX_AGE or entry.created_at > now:
            return self._path_miss(path, "expired", "cache-created-at+7-days")
        try:
            stat = document.stat()
        except OSError:
            return self._path_miss(path, "file_unavailable", _CHEAP_METHOD)
        identity = entry.identity
        if (
            identity.normalized_path != normalized_document_path(document)
            or identity.size != stat.st_size
            or identity.mtime_ns != stat.st_mtime_ns
        ):
            return self._path_miss(path, "cheap_identity_mismatch", _CHEAP_METHOD)
        try:
            digest = self._hash_file(document)
        except OSError:
            return self._path_miss(path, "file_unavailable", _HASH_METHOD)
        if digest != identity.partial_sha256:
            return self._path_miss(path, "content_hash_mismatch", _HASH_METHOD)
        metadata = _metadata(hit=True, method=_HASH_METHOD, created_at=entry.created_at)
        return ConventionCacheLookup(
            entry.profile.model_copy(update={"cache": metadata}), metadata
        )

    def store(
        self,
        document: Path,
        profile: DocumentConventionProfile,
    ) -> ConventionCacheMetadata:
        try:
            stat = document.stat()
            digest = self._hash_file(document)
        except OSError:
            return _metadata(hit=False, method=_HASH_METHOD)
        created_at = self._clock()
        entry = _PersistentEntry(
            schema_version=_SCHEMA_VERSION,
            created_at=created_at,
            identity=_FileIdentity(
                normalized_path=normalized_document_path(document),
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                partial_sha256=digest,
            ),
            profile=profile.model_copy(update={"cache": None}),
        )
        lock = PreviewFileLock.try_acquire(self.lock_path(document))
        if lock is None:
            return _metadata(hit=False, method=_HASH_METHOD)
        path = self.cache_path(document)
        partial = path.with_name(f".{path.name}.partial-{uuid4().hex}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            encoded = entry.model_dump_json(exclude_none=True).encode("utf-8")
            with partial.open("xb") as stream:
                _ = stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(partial, path)
            if path in self._read_misses:
                self._deferred_hits.add(path)
        except OSError:
            partial.unlink(missing_ok=True)
            return _metadata(hit=False, method=_HASH_METHOD)
        finally:
            lock.close()
        return _metadata(hit=False, method=_HASH_METHOD, created_at=created_at)

    def finish_public_read(self) -> None:
        self._deferred_hits.clear()
        self._read_misses.clear()
