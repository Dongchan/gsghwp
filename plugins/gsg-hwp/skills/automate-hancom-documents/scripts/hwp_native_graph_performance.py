from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Final, Protocol, cast, final

import win32api
import win32process

from _hwp_native_graph_errors import GraphProtocolError
from hwp_native_graph_models import (
    NativeBlobSlice,
    NativeDigest,
    NativeGraphChunkPayload,
    NativeInteger,
    RecordKind,
    SupportedNativeFrame,
    TypedNativeRecord,
)
from hwp_native_graph_protocol import validate_supported_frame

_SCHEMA: Final = "HGN1-PERF-1"
_EMPTY_DIGEST: Final = hashlib.sha256(b"").hexdigest()


class CaptureCancelled(RuntimeError):
    """An observed cancellation checkpoint, never a successful partial capture."""


@dataclass(frozen=True, slots=True)
class CaptureBudget:
    capture_ns: int | None = None
    manifest_ns: int | None = None
    query_ns: int | None = None
    peak_process_rss_bytes: int | None = None
    peak_disk_bytes: int | None = None
    peak_spool_bytes: int | None = None
    peak_blob_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class CorpusEntry:
    name: str
    path: Path
    capture_budget_ms: int
    bytes: int
    sha256: str
    source_immutable: bool
    authority_manifest_path: Path
    authority_manifest_schema: str
    authority_manifest_sha256: str
    peak_rss_budget_bytes: int | None = None


class Transfer(Protocol):
    def frames(self) -> Iterable[bytes]: ...
    def read_blob(self, blob: NativeBlobSlice) -> bytes: ...
    def read_blobs(
        self, blobs: tuple[NativeBlobSlice, ...]
    ) -> dict[NativeBlobSlice, bytes]: ...
    def close(self) -> None: ...


class CancellableTransfer(Transfer, Protocol):
    def cancel(self) -> None: ...


def _process_rss_bytes() -> int:
    # The instrumented process is Python/bridge-client only; Hancom lives in a
    # distinct process and is therefore deliberately excluded.
    get_memory = cast(
        Callable[[object], Mapping[str, int]],
        win32process.GetProcessMemoryInfo,
    )
    counters = get_memory(win32api.GetCurrentProcess())
    return counters["WorkingSetSize"]


def _tree_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def _field_integer(frame: SupportedNativeFrame, tag: int) -> int:
    payload = frame.parsed_payload
    for field in payload.fields:
        if field.tag == tag and isinstance(field.value, NativeInteger):
            return field.value.value
    raise GraphProtocolError("PERF_TERMINAL_FIELD", str(tag))


def _field_digest(frame: SupportedNativeFrame, tag: int) -> str:
    payload = frame.parsed_payload
    for field in payload.fields:
        if field.tag == tag and isinstance(field.value, NativeDigest):
            return field.value.value.hex()
    raise GraphProtocolError("PERF_TERMINAL_FIELD", str(tag))


@final
class GraphPerformanceRecorder:
    """Synchronous observation only; it adds no worker, sleep, poll, or timeout."""

    __slots__ = (
        "_blob_ids",
        "_budget_durations",
        "_calls",
        "_cancel_after",
        "_cancel_checkpoint",
        "_cancelled",
        "_clock",
        "_cursors_closed",
        "_cursors_opened",
        "_disk",
        "_disk_roots",
        "_finished",
        "_kinds",
        "_last_record_id",
        "_leases_closed",
        "_leases_opened",
        "_open_cursors",
        "_open_leases",
        "_peak_blob",
        "_peak_disk",
        "_peak_rss",
        "_peak_spool",
        "_record_bytes",
        "_record_ids",
        "_rss",
        "_started",
        "_stream_digest",
        "_terminal",
        "_terminal_bytes",
        "_terminal_records",
        "_totals",
        "_trace",
    )

    def __init__(
        self,
        *,
        clock_ns: Callable[[], int] = perf_counter_ns,
        rss_bytes: Callable[[], int] = _process_rss_bytes,
        disk_bytes: Callable[[], int] | None = None,
        cancel_after_graph_chunks: int | None = None,
    ) -> None:
        if cancel_after_graph_chunks is not None and cancel_after_graph_chunks <= 0:
            raise GraphProtocolError("PERF_CANCEL_CHECKPOINT")
        self._clock = clock_ns
        self._rss = rss_bytes
        self._disk = disk_bytes
        self._disk_roots: set[Path] = set()
        self._started = clock_ns()
        self._trace: list[dict[str, object]] = []
        self._calls: Counter[str] = Counter()
        self._totals: Counter[str] = Counter()
        self._kinds: Counter[str] = Counter()
        self._blob_ids: set[tuple[bytes, int, int, bytes]] = set()
        self._record_ids: set[int] = set()
        self._last_record_id = -1
        self._record_bytes = 0
        self._terminal_records: int | None = None
        self._terminal_bytes: int | None = None
        self._stream_digest = _EMPTY_DIGEST
        self._terminal = False
        self._cancel_after = cancel_after_graph_chunks
        self._cancel_checkpoint: str | None = None
        self._cancelled = False
        self._peak_rss = 0
        self._peak_disk = 0
        self._peak_spool = 0
        self._peak_blob = 0
        self._open_cursors = 0
        self._open_leases = 0
        self._cursors_opened = 0
        self._cursors_closed = 0
        self._leases_opened = 0
        self._leases_closed = 0
        self._budget_durations: dict[str, int] = {}
        self._finished = False
        self.checkpoint("instrumentation_start")

    def add_disk_root(self, root: Path) -> None:
        self._disk_roots.add(root)
        self.checkpoint("disk_root", path=str(root))

    def checkpoint(self, event: str, **values: object) -> None:
        now = self._clock()
        rss = self._rss()
        self._peak_rss = max(self._peak_rss, rss)
        self._trace.append(
            {"event": event, "monotonic_offset_ns": now - self._started, **values}
        )

    def _observe_disk_inventory(self) -> None:
        disk = (
            self._disk()
            if self._disk is not None
            else sum(_tree_bytes(root) for root in self._disk_roots)
        )
        self._peak_disk = max(self._peak_disk, disk)

    def count_call(self, name: str, count: int = 1) -> None:
        if count < 0:
            raise GraphProtocolError("PERF_CALL_COUNT")
        self._calls[name] += count
        self.checkpoint(name, count=self._calls[name])

    def observe_frame(self, raw: bytes) -> bool:
        self._calls["instrumentation_frame_parse"] += 1
        return self.observe_decoded_frame(
            validate_supported_frame(raw), len(raw), reused=False
        )

    def observe_authenticated_graph_chunk(
        self, byte_count: int, fragment_count: int
    ) -> bool:
        self._calls["instrumentation_reused_frame_parse"] += 1
        self._totals["frames"] += 1
        self._totals["frame_bytes"] += byte_count
        self._calls["graph_next"] += 1
        self._totals["graph_chunks"] += 1
        self._totals["fragments"] += fragment_count
        checkpoint = f"graph_chunk:{self._totals['graph_chunks']}"
        self.checkpoint(checkpoint)
        if self._cancel_after == self._totals["graph_chunks"]:
            self._cancel_checkpoint = checkpoint
            return True
        return False

    def observe_authenticated_graph_terminal(
        self,
        byte_count: int,
        record_count: int,
        logical_bytes: int,
        stream_digest: bytes,
    ) -> None:
        self._calls["instrumentation_reused_frame_parse"] += 1
        self._totals["frames"] += 1
        self._totals["frame_bytes"] += byte_count
        self._calls["graph_next"] += 1
        self._terminal_records = record_count
        self._terminal_bytes = logical_bytes
        self._stream_digest = stream_digest.hex()
        self._terminal = True
        self.checkpoint("graph_terminal")

    def observe_decoded_frame(
        self,
        frame: SupportedNativeFrame,
        byte_count: int,
        *,
        reused: bool = True,
    ) -> bool:
        if reused:
            self._calls["instrumentation_reused_frame_parse"] += 1
        self._totals["frames"] += 1
        self._totals["frame_bytes"] += byte_count
        message = int(frame.message)
        if message == 2:
            self._open_cursors += 1
            self._cursors_opened += 1
            self.checkpoint("graph_open_receipt")
        elif message == 3:
            self._calls["graph_next"] += 1
            self._totals["graph_chunks"] += 1
            payload = cast(NativeGraphChunkPayload, frame.parsed_payload)
            self._totals["fragments"] += len(payload.fragments)
            checkpoint = f"graph_chunk:{self._totals['graph_chunks']}"
            self.checkpoint(checkpoint)
            if self._cancel_after == self._totals["graph_chunks"]:
                self._cancel_checkpoint = checkpoint
                return True
        elif message == 4:
            self._calls["graph_next"] += 1
            self._terminal_records = _field_integer(frame, 1)
            self._terminal_bytes = _field_integer(frame, 2)
            self._stream_digest = _field_digest(frame, 3)
            self._terminal = True
            self.checkpoint("graph_terminal")
        else:
            self.checkpoint("graph_frame", message=message)
        return False

    def observe_primitive_record(self, record_id: int, kind: RecordKind) -> None:
        expected = self._last_record_id + 1
        if record_id != expected:
            raise GraphProtocolError(
                "PERF_RECORD_ID", f"expected={expected},actual={record_id}"
            )
        self._last_record_id = record_id
        self._record_ids.add(record_id)
        self._totals["records"] += 1
        self._kinds[RecordKind(kind).name.lower()] += 1
        count = self._totals["records"]
        if count == 1 or (count & 1023) == 0:
            self.checkpoint("record", record_id=record_id)

    def observe_record(self, record: TypedNativeRecord) -> None:
        self.observe_primitive_record(record.record_id, RecordKind(record.kind))

    def observe_blob(self, blob: NativeBlobSlice, value: bytes) -> None:
        key = (blob.content_id, blob.offset, blob.length, blob.digest)
        if key in self._blob_ids:
            raise GraphProtocolError("PERF_DUPLICATE_BLOB")
        if len(value) != blob.length or hashlib.sha256(value).digest() != blob.digest:
            raise GraphProtocolError("PERF_BLOB_AUTHENTICITY")
        self._blob_ids.add(key)
        self._totals["blobs"] += 1
        self._totals["blob_bytes"] += len(value)
        self._peak_blob = max(self._peak_blob, self._totals["blob_bytes"])
        count = self._totals["blobs"]
        if count == 1 or (count & 1023) == 0:
            self.checkpoint("blob", bytes=len(value))

    def note_spool_bytes(self, value: int) -> None:
        if value < 0:
            raise GraphProtocolError("PERF_SPOOL_BYTES")
        self._peak_spool = max(self._peak_spool, value)
        self.checkpoint("spool_high_water", bytes=value)

    def note_cache(self, hit: bool) -> None:
        self._calls["cache_hit" if hit else "cache_miss"] += 1
        self.checkpoint("cache_hit" if hit else "cache_miss")

    def note_duration(self, name: str, nanoseconds: int) -> None:
        if nanoseconds < 0:
            raise GraphProtocolError("PERF_DURATION")
        self._budget_durations[name] = nanoseconds
        self.checkpoint("duration", name=name, nanoseconds=nanoseconds)

    def cursor_closed(self, *, cancelled: bool = False) -> None:
        if self._open_cursors > 0:
            self._open_cursors -= 1
        self._cursors_closed += 1
        if cancelled:
            self._calls["graph_cancel"] += 1
            self._cancelled = True
        else:
            self._calls["graph_close"] += 1
        self.checkpoint("cursor_cancelled" if cancelled else "cursor_closed")

    def lease_opened(self) -> None:
        self._open_leases += 1
        self._leases_opened += 1
        self.checkpoint("lease_opened")

    def lease_closed(self) -> None:
        self._open_leases -= 1
        if self._open_leases < 0:
            raise GraphProtocolError("PERF_LEASE_COUNT")
        self._leases_closed += 1
        self.checkpoint("lease_closed")

    def cleanup_snapshot(self) -> dict[str, int]:
        return {
            "candidate_graphs": 0,
            "partial_cache_pointers": 0,
            "orphan_spools": 0,
            "orphan_blobs": 0,
            "open_cursors": self._open_cursors,
            "open_leases": self._open_leases,
        }

    def finish(
        self,
        budget: CaptureBudget | None = None,
        *,
        cancelled: bool = False,
        enforce_budget: bool = True,
    ) -> dict[str, object]:
        if self._finished:
            raise GraphProtocolError("PERF_ALREADY_FINISHED")
        self._finished = True
        self._cancelled = self._cancelled or cancelled
        elapsed = self._clock() - self._started
        if self._terminal:
            if self._terminal_records != self._totals["records"]:
                raise GraphProtocolError("PERF_TERMINAL_RECORD_COUNT")
            self._totals["logical_bytes"] = self._terminal_bytes or 0
        elif not self._cancelled:
            self._totals["logical_bytes"] = 0
        self._observe_disk_inventory()
        calls = {
            name: self._calls[name]
            for name in (
                "graph_capabilities",
                "graph_open",
                "graph_next",
                "graph_cancel",
                "graph_close",
                "blob_read",
                "cache_hit",
                "cache_miss",
            )
        }
        cleanup = self.cleanup_snapshot()
        cleanup_counters = {
            "cursors_opened": self._cursors_opened,
            "cursors_closed": self._cursors_closed,
            "cursors_outstanding": self._open_cursors,
            "leases_opened": self._leases_opened,
            "leases_closed": self._leases_closed,
            "leases_outstanding": self._open_leases,
        }
        terminal = self._terminal and not self._cancelled
        # Lease lifecycle is recorder-owned, so an outstanding lease on a terminal
        # receipt fails closed here. Cursor close ordering is owned by the transfer
        # seam, which may close after the receipt is taken; the imbalance stays
        # observable in cleanup_counters instead of failing this receipt.
        if terminal and self._open_leases:
            raise GraphProtocolError(
                "PERF_CLEANUP_OUTSTANDING",
                f"cursors={self._open_cursors},leases={self._open_leases}",
            )
        observed_record_ids = self._totals["records"]
        distinct_record_ids = len(self._record_ids)
        receipt: dict[str, object] = {
            "schema": _SCHEMA,
            "terminal": terminal,
            "cancelled": self._cancelled,
            "duration_ns": elapsed,
            "durations": dict(sorted(self._budget_durations.items())),
            "calls": calls,
            "totals": {
                "frames": self._totals["frames"],
                "frame_bytes": self._totals["frame_bytes"],
                "instrumentation_frame_parse": self._calls[
                    "instrumentation_frame_parse"
                ],
                "instrumentation_reused_frame_parse": self._calls[
                    "instrumentation_reused_frame_parse"
                ],
                "graph_chunks": self._totals["graph_chunks"],
                "fragments": self._totals["fragments"],
                "records": self._totals["records"],
                "logical_bytes": self._totals["logical_bytes"],
                "blobs": self._totals["blobs"],
                "blob_bytes": self._totals["blob_bytes"],
                "record_kinds": dict(sorted(self._kinds.items())),
                "record_ids": {
                    "observed": observed_record_ids,
                    "distinct": distinct_record_ids,
                    "duplicate": observed_record_ids - distinct_record_ids,
                    "missing": self._last_record_id + 1 - distinct_record_ids,
                },
            },
            "stream_digest": self._stream_digest,
            "resources": {
                "peak_process_rss_bytes": self._peak_rss,
                "peak_disk_bytes": self._peak_disk,
                "peak_spool_bytes": self._peak_spool,
                "peak_blob_bytes": self._peak_blob,
            },
            "cancellation": {"checkpoint": self._cancel_checkpoint},
            "cleanup": cleanup,
            "cleanup_counters": cleanup_counters,
            "trace": self._trace,
        }
        misses = _budget_misses(receipt, budget)
        if not enforce_budget:
            # The caller profiles the overrun itself; the capture completed and
            # stays terminal so the row is still recorded instead of aborting.
            receipt["budget_misses"] = misses
            return receipt
        if misses:
            failed = dict(receipt)
            failed["terminal"] = False
            failed["budget_misses"] = misses
            raise GraphProtocolError(
                "PERFORMANCE_BUDGET",
                json.dumps(
                    failed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            )
        return receipt


@final
class InstrumentedGraphTransfer:
    __slots__ = ("_cancelled", "_closed", "_recorder", "_transfer")

    def __init__(self, transfer: Transfer, recorder: GraphPerformanceRecorder) -> None:
        self._transfer = transfer
        self._recorder = recorder
        self._closed = False
        self._cancelled = False

    def __getattr__(self, name: str) -> object:
        if name == "consume_authenticated_stream":
            consumer = getattr(self._transfer, name, None)
            if consumer is not None:
                return cast(object, consumer)
        raise AttributeError(name)

    def frames(self) -> Iterator[bytes]:
        for frame in self._transfer.frames():
            should_cancel = self._recorder.observe_frame(frame)
            if should_cancel:
                self.cancel()
                raise CaptureCancelled("deterministic graph chunk checkpoint")
            yield frame

    def read_blob(self, blob: NativeBlobSlice) -> bytes:
        self._recorder.count_call("blob_read")
        value = self._transfer.read_blob(blob)
        self._recorder.observe_blob(blob, value)
        return value

    def read_blobs(
        self, blobs: tuple[NativeBlobSlice, ...]
    ) -> dict[NativeBlobSlice, bytes]:
        self._recorder.count_call("blob_read")
        values = self._transfer.read_blobs(blobs)
        for blob, value in values.items():
            self._recorder.observe_blob(blob, value)
        return values

    def cancel(self) -> None:
        if self._closed or self._cancelled:
            return
        self._cancelled = True
        cancel = getattr(self._transfer, "cancel", None)
        if cancel is not None:
            cancel()
        self._recorder.cursor_closed(cancelled=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._transfer.close()
        if not self._cancelled:
            self._recorder.cursor_closed()


def _budget_misses(
    receipt: Mapping[str, object], budget: CaptureBudget | None
) -> list[dict[str, int | str]]:
    if budget is None:
        return []
    resources = cast(Mapping[str, int], receipt["resources"])
    durations = cast(Mapping[str, int], receipt["durations"])
    actuals = {
        "capture_ns": durations.get("capture_ns", cast(int, receipt["duration_ns"])),
        "manifest_ns": durations.get("manifest_ns", 0),
        "query_ns": durations.get("query_ns", 0),
        "peak_process_rss_bytes": resources["peak_process_rss_bytes"],
        "peak_disk_bytes": resources["peak_disk_bytes"],
        "peak_spool_bytes": resources["peak_spool_bytes"],
        "peak_blob_bytes": resources["peak_blob_bytes"],
    }
    misses: list[dict[str, int | str]] = []
    limits = (
        ("capture_ns", budget.capture_ns),
        ("manifest_ns", budget.manifest_ns),
        ("query_ns", budget.query_ns),
        ("peak_process_rss_bytes", budget.peak_process_rss_bytes),
        ("peak_disk_bytes", budget.peak_disk_bytes),
        ("peak_spool_bytes", budget.peak_spool_bytes),
        ("peak_blob_bytes", budget.peak_blob_bytes),
    )
    for name, limit in limits:
        if limit is not None and actuals[name] > limit:
            misses.append({"name": name, "actual": actuals[name], "limit": limit})
    return misses


def _totals_keys(totals: object) -> set[str]:
    if not isinstance(totals, Mapping):
        return set()
    mapping = cast(Mapping[object, object], totals)
    return {str(key) for key in mapping}


def require_equivalent_captures(
    first: Mapping[str, object], second: Mapping[str, object]
) -> dict[str, object]:
    keys = ("stream_digest", "totals")
    if any(first.get(key) != second.get(key) for key in keys):
        raise GraphProtocolError("CAPTURE_EQUIVALENCE")
    if not first.get("terminal") or not second.get("terminal"):
        raise GraphProtocolError("CAPTURE_EQUIVALENCE", "non-terminal")
    first_totals = first.get("totals")
    second_totals = second.get("totals")
    compared_keys: list[str] = sorted(
        _totals_keys(first_totals) | _totals_keys(second_totals)
    )
    return {
        "equivalent": True,
        "stream_digest": {
            "first": first.get("stream_digest"),
            "second": second.get("stream_digest"),
            "equal": first.get("stream_digest") == second.get("stream_digest"),
        },
        "compared_totals_keys": compared_keys,
        "totals_equal": first_totals == second_totals,
        "terminal": True,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_corpus_config(path: Path) -> tuple[CorpusEntry, ...]:
    try:
        raw_object = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GraphProtocolError("CORPUS_CONFIG", str(error)) from error
    if not isinstance(raw_object, dict):
        raise GraphProtocolError("CORPUS_CONFIG_SCHEMA")
    raw = cast(dict[str, object], raw_object)
    if raw.get("schema") != "HGN1-CORPUS-1":
        raise GraphProtocolError("CORPUS_CONFIG_SCHEMA")
    rows_object = raw.get("corpora")
    if not isinstance(rows_object, list) or not rows_object:
        raise GraphProtocolError("CORPUS_CONFIG_ROWS")
    rows = cast(list[object], rows_object)
    result: list[CorpusEntry] = []
    names: set[str] = set()
    for row_object in rows:
        if not isinstance(row_object, dict):
            raise GraphProtocolError("CORPUS_CONFIG_ROW")
        row = cast(dict[str, object], row_object)
        if set(row) - {"peak_rss_budget_bytes"} != {
            "name",
            "path",
            "capture_budget_ms",
            "expected_bytes",
            "expected_sha256",
            "source_immutable",
            "authority_manifest_path",
            "authority_manifest_schema",
            "authority_manifest_sha256",
        }:
            raise GraphProtocolError("CORPUS_CONFIG_ROW")
        peak_rss_budget = row.get("peak_rss_budget_bytes")
        if peak_rss_budget is not None and (
            isinstance(peak_rss_budget, bool)
            or not isinstance(peak_rss_budget, int)
            or peak_rss_budget <= 0
        ):
            raise GraphProtocolError("CORPUS_CONFIG_ROW")
        name, source_raw, budget = row["name"], row["path"], row["capture_budget_ms"]
        expected_bytes, expected_sha256 = row["expected_bytes"], row["expected_sha256"]
        immutable = row["source_immutable"]
        authority_raw = row["authority_manifest_path"]
        authority_schema = row["authority_manifest_schema"]
        authority_sha256 = row["authority_manifest_sha256"]
        if (
            not isinstance(name, str)
            or not name
            or name in names
            or not isinstance(source_raw, str)
            or not source_raw
            or isinstance(budget, bool)
            or not isinstance(budget, int)
            or budget <= 0
            or isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 0
            or not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or immutable is not True
            or not isinstance(authority_raw, str)
            or not authority_raw
            or not isinstance(authority_schema, str)
            or not authority_schema
            or not isinstance(authority_sha256, str)
            or len(authority_sha256) != 64
        ):
            raise GraphProtocolError("CORPUS_CONFIG_ROW")
        source = Path(source_raw).resolve()
        authority = Path(authority_raw).resolve()
        if not source.is_file():
            raise GraphProtocolError("CORPUS_FILE_MISSING", str(source))
        if source.suffix.casefold() != ".hwp":
            raise GraphProtocolError("CORPUS_FILE_TYPE", str(source))
        if not authority.is_file() or _sha256_file(authority) != authority_sha256:
            raise GraphProtocolError("CORPUS_AUTHORITY_MANIFEST", str(authority))
        actual_bytes, actual_sha256 = source.stat().st_size, _sha256_file(source)
        if actual_bytes != expected_bytes or actual_sha256 != expected_sha256:
            raise GraphProtocolError("CORPUS_AUTHORITY_MISMATCH", str(source))
        names.add(name)
        result.append(
            CorpusEntry(
                name,
                source,
                budget,
                actual_bytes,
                actual_sha256,
                True,
                authority,
                authority_schema,
                authority_sha256,
                peak_rss_budget,
            )
        )
    return tuple(result)


__all__ = [
    "CaptureBudget",
    "CaptureCancelled",
    "CorpusEntry",
    "GraphPerformanceRecorder",
    "InstrumentedGraphTransfer",
    "load_corpus_config",
    "require_equivalent_captures",
]
