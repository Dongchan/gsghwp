from __future__ import annotations

import os
import secrets
import shutil
import sys
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from typing import IO, Final, Literal, final

from hwp_checkpoint_signature import checkpoint_signature_is_complete
from hwp_errors import HwpLiveError
from hwp_live_edit_history_policy import RecoveryFilePair, RollbackRecoveryRetention
from hwp_live_native_action_models import (
    NativeActionCommand,
    NativeSelection,
    SaveDocumentFileCommand,
)


type HistoryDirection = Literal["undo", "redo"]
type HistoryOperation = Literal[
    "control.delete",
    "document.delete_page",
    "text.patch",
    "document.append_layout",
    "document.insert_layout",
    "table.build_series",
    "table.repeat_template",
]


MAX_NATIVE_HISTORY_STEPS: Final = 100
_SIGNED_CHECKPOINT_MAGIC: Final = b"GSG_HWP_ENCODED_BLOCK_V2\n"
_LEGACY_CHECKPOINT_MAGIC: Final = b"GSG_HWP_ENCODED_BLOCK_V1\n"
# A document-file checkpoint is the document itself, so it has no header of
# ours to read. What a restore needs is written beside it instead.
_DOCUMENT_FILE_META_MAGIC: Final = b"GSG_HWP_DOCUMENT_FILE_V1\n"
_DOCUMENT_FILE_META_SUFFIX: Final = ".gsgmeta"
# A restore deletes the whole document before it inserts anything, so the bridge
# saves a way back beside the checkpoint first
# (ActionLifecycle.cpp, CheckpointRollbackPath). The bridge deletes it however
# the restore ends, but it is a whole copy of a document and the one thing that
# cannot delete it is the process that died holding it. Nothing on this side
# knew the name, so a leftover survived every prune and only went away with the
# whole session directory.
_DOCUMENT_FILE_ROLLBACK_SUFFIX: Final = ".rollback"
_CHECKPOINT_SIGNATURE_MAX_BYTES: Final = 256
_CHECKPOINT_META_MAX_BYTES: Final = 4096
_HISTORY_DIRECTORY_PREFIX: Final = "gsg-hwp-live-history-"
# A killed process leaves whole copies of documents behind with nothing left
# running to delete them.
#
# The age alone does not say a directory is abandoned. mtime only moves when a
# checkpoint is written, so a session that is open but has not been edited for a
# day looks exactly like a dead one — and deleting it takes that session's
# checkpoints out from under it, which is where its hwp_undo was.
#
# So every live session holds a lock file open for as long as it owns the
# directory, and the sweep asks the filesystem whether anyone still does. **The
# unlocked directory is swept on sight.** Nobody owns it, nothing can be taken
# out from under anyone, and each one is a whole copy of a document — a live
# measurement found 1.75 GB across seven directories with three of them
# ownerless.
#
# The age gate only covers the seconds between a directory being created and its
# lock file being written; a sweep that listed the parent in that window would
# otherwise take a brand-new session for a dead one. It is a race guard, not a
# retention period. Twenty-four hours used to sit here and was read as "delete
# after a day", which is backwards: a day is the outer bound by which nothing
# should be left, not the wait before cleaning starts.
_UNOWNED_HISTORY_GRACE_SECONDS: Final = 60.0
# Held open by the owning process. On Windows an open file cannot be deleted by
# anyone else, so "the unlink succeeded" is the proof that nobody owns this
# directory any more; a process that was killed holds nothing.
#
# It sits beside the directory rather than inside it on purpose. Inside, the
# handle this process holds would be the one thing standing between the
# directory and its own removal, and a store that is dropped without cleanup()
# would leave the whole thing behind instead of a few bytes.
_SESSION_LOCK_SUFFIX: Final = ".owner-lock"
_ROLLBACK_RECOVERY_SWEEP_SECONDS: Final = 60.0

# How much checkpoint data one live session may keep on disk at once.
#
# This is a disk bound, not a document limit, and it is the only thing that
# decides which documents can be checkpointed at all: the gate that skips the
# capture is derived from it (`MAX_CHECKPOINT_SOURCE_FILE_BYTES`), so the two
# cannot drift apart the way they had.
#
# It used to be 512 MiB with the gate written out separately as 192 MiB, and
# neither number could be derived from the other. The field document that
# exposed this is 331 MB: the gate turned the capture off, and the budget it was
# supposedly protecting was never consulted, because one edit's before/after
# pair (662 MB) would not have fitted it either. A budget that cannot hold a
# single edit is not a budget, it is an outage, so the number is chosen the
# other way round now: it must hold the before/after pair and a retained rollback
# of the 331 MB field document.
#
# It stays finite on purpose. Each checkpoint is a whole copy of the document,
# a measurement on this repository once found 1.75 GB of them across seven
# abandoned session directories, and unbounded growth is how that happened.
# The budget and `_max_checkpoint_files` bound ordinary before/after history.
# A failed restore's rollback is still counted in the measured usage, but those
# limits may evict only ordinary checkpoints, never the user's last recovery
# copy. Evicted-entry rollbacks remain registered until explicit discard, session
# cleanup, or the separate 24-hour expiry in hwp_live_edit_history_policy.py.
MAX_HISTORY_DISK_BYTES: Final = 1024 * 1024 * 1024
# The largest document a checkpoint may be taken of: one before/after pair plus
# the rollback copy a failed restore deliberately keeps must fit the budget.
#
# Measured against the document on disk, which is an over-estimate of the
# checkpoint: the bridge saves its copy with `prvimage:0;prvtext:0`, so the copy
# drops a preview image the original carries. Erring high costs an undo entry;
# erring low costs two whole-document saves that `validate_capacity` then throws
# away, which is worse.
MAX_CHECKPOINT_SOURCE_FILE_BYTES: Final = MAX_HISTORY_DISK_BYTES // 3


def _session_lock_path(directory: Path) -> Path:
    return directory.with_name(directory.name + _SESSION_LOCK_SUFFIX)


@dataclass(frozen=True, slots=True)
class DocumentCheckpointEvidence:
    """What the bridge said it did while writing a checkpoint.

    ``capture_method`` is the path that actually ran (ActionLifecycle.cpp), so a
    caller can tell the preferred on-disk copy from the legacy in-memory block
    instead of guessing which one the bridge chose. ``identity_restored`` is the
    one that matters on its own: ``SaveAs`` moved the editing session onto the
    checkpoint copy — no documented SaveAs option prevents that, which is why
    the bridge checks instead of assuming — and the bridge had to save the
    user's own file to move it back. That is a write to the user's document that
    nobody asked for, and it must not be invisible.

    When the bridge could not move the session back it reports
    ``DOCUMENT_CHECKPOINT_IDENTITY`` instead, and that is not evidence about a
    checkpoint at all; see ``DocumentIdentityError``.
    """

    capture_method: str = ""
    identity_restored: bool = False
    # Why the preferred on-disk capture did not run, when the bridge said so.
    # The fallback that runs next can succeed, and then `capture_method` reads
    # ``encoded_block`` with nothing to show that the better path was refused —
    # which is how a silent degradation stays silent. Empty means the bridge
    # said nothing, which is "unknown", never "nothing went wrong".
    unavailable_reason: str = ""

    def merged(
        self,
        other: "DocumentCheckpointEvidence",
    ) -> "DocumentCheckpointEvidence":
        return DocumentCheckpointEvidence(
            capture_method=other.capture_method or self.capture_method,
            identity_restored=self.identity_restored or other.identity_restored,
            unavailable_reason=(self.unavailable_reason or other.unavailable_reason),
        )


NO_CHECKPOINT_EVIDENCE: Final = DocumentCheckpointEvidence()


@dataclass(frozen=True, slots=True)
class DocumentCheckpoint:
    path: Path
    bytes: int
    page_count: int
    # Content signature of the document at the moment this checkpoint was
    # captured, as written by the native bridge (ActionLifecycle.cpp) either
    # into the checkpoint file itself (GSG_HWP_ENCODED_BLOCK_V2) or into its
    # sidecar (GSG_HWP_DOCUMENT_FILE_V1). Empty when the bridge predates the
    # signed layout or could not capture one; callers must then treat every
    # content comparison against this checkpoint as unavailable rather than as
    # a match.
    signature: str = ""


def checkpoint_meta_path(path: Path) -> Path:
    """Sidecar of a document-file checkpoint, whether or not it exists."""
    return path.with_name(path.name + _DOCUMENT_FILE_META_SUFFIX)


def _checkpoint_recovery_files(path: Path) -> RecoveryFilePair:
    rollback = path.with_name(path.name + _DOCUMENT_FILE_ROLLBACK_SUFFIX)
    return rollback, checkpoint_meta_path(rollback)


def checkpoint_files(path: Path) -> tuple[Path, ...]:
    """Every file one checkpoint owns.

    A checkpoint is one document-sized file plus, when the bridge wrote the
    document-file layout, a few bytes of metadata beside it. Deleting a
    checkpoint means deleting both.

    The two rollback names are here for the same reason. Restoring this
    checkpoint makes the bridge write a document-sized copy of the live document
    beside it first, and the bridge deletes that copy itself on every path it
    can still run on. The one it cannot run on is a process that was killed
    mid-restore, and what it leaves behind is another whole document. Names that
    are usually absent cost one failed unlink each; a document-sized file that
    nothing owns is retained until explicit discard, session cleanup, or its
    24-hour recovery expiry.
    """
    rollback, rollback_metadata = _checkpoint_recovery_files(path)
    return (
        path,
        checkpoint_meta_path(path),
        rollback,
        rollback_metadata,
    )


def _signature_line(text: bytes) -> str:
    try:
        return text.decode("ascii")
    except UnicodeDecodeError:
        return ""


def _read_document_file_signature(path: Path) -> str:
    try:
        with checkpoint_meta_path(path).open("rb") as source:
            text = source.read(_CHECKPOINT_META_MAX_BYTES)
    except OSError:
        return ""
    if not text.startswith(_DOCUMENT_FILE_META_MAGIC):
        return ""
    for line in text[len(_DOCUMENT_FILE_META_MAGIC) :].split(b"\n"):
        if line.startswith(b"SIG "):
            return _signature_line(line[4:] if line.startswith(b"SIG SIG ") else line)
    return ""


def read_checkpoint_signature(path: Path) -> str:
    """Content signature stored with a checkpoint, or "" when it has none.

    The checkpoint says which layout it is by its own first bytes, so an
    encoded block written by an older bridge still reads back the same way.
    Only the header is read: a checkpoint can be hundreds of megabytes and
    nothing here needs the rest of it.
    """
    try:
        with path.open("rb") as source:
            header = source.read(
                len(_SIGNED_CHECKPOINT_MAGIC) + _CHECKPOINT_SIGNATURE_MAX_BYTES
            )
    except OSError:
        return ""
    if header.startswith(_LEGACY_CHECKPOINT_MAGIC):
        return ""
    if not header.startswith(_SIGNED_CHECKPOINT_MAGIC):
        return _read_document_file_signature(path)
    line, separator, _ = header[len(_SIGNED_CHECKPOINT_MAGIC) :].partition(b"\n")
    if not separator:
        return ""
    return _signature_line(line)


def _checkpoint_is_document_file(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            header = source.read(
                max(len(_SIGNED_CHECKPOINT_MAGIC), len(_LEGACY_CHECKPOINT_MAGIC))
            )
    except OSError:
        return False
    return not (
        header.startswith(_SIGNED_CHECKPOINT_MAGIC)
        or header.startswith(_LEGACY_CHECKPOINT_MAGIC)
    )


@contextmanager
def checkpoint_restore_metadata(
    checkpoint: DocumentCheckpoint,
    full_name: str,
    *,
    engine_undo_origin: DocumentCheckpoint | None = None,
) -> Generator[None, None, None]:
    """Materialize document-file metadata only while the native restore reads it."""

    if not _checkpoint_is_document_file(checkpoint.path):
        yield
        return
    document_format = "HWPX" if Path(full_name).suffix.casefold() == ".hwpx" else "HWP"
    metadata = _DOCUMENT_FILE_META_MAGIC + f"FMT {document_format}\n".encode("ascii")
    if checkpoint.signature:
        try:
            signature = checkpoint.signature.encode("ascii")
        except UnicodeEncodeError as error:
            raise HwpLiveError(
                "문서 체크포인트 내용 서명이 ASCII가 아닙니다"
            ) from error
        metadata += b"SIG " + signature + b"\n"
    if engine_undo_origin is not None:
        if not checkpoint.signature or not engine_undo_origin.signature:
            raise HwpLiveError(
                "한컴 단일 텍스트 실행 취소에는 편집 전후 내용 서명이 모두 필요합니다"
            )
        try:
            origin_signature = engine_undo_origin.signature.encode("ascii")
        except UnicodeEncodeError as error:
            raise HwpLiveError("편집 후 문서 내용 서명이 ASCII가 아닙니다") from error
        metadata += b"HISTORY P1_SINGLE_TEXT_UNDO\n"
        metadata += b"ORIGIN " + origin_signature + b"\n"
        metadata += f"ORIGIN_PAGES {engine_undo_origin.page_count}\n".encode("ascii")
    path = checkpoint_meta_path(checkpoint.path)
    try:
        _ = path.write_bytes(metadata)
    except OSError as error:
        raise HwpLiveError("문서 체크포인트 메타 파일을 준비하지 못했습니다") from error
    try:
        yield
    finally:
        active_error = sys.exception()
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            cleanup_error = HwpLiveError(
                "문서 체크포인트 메타 파일을 정리하지 못했습니다"
            )
            if active_error is None:
                raise cleanup_error from error
            active_error.add_note(f"{cleanup_error}: {error}")


def _session_is_still_running(directory: Path) -> bool:
    """Whether some process still owns this history directory.

    The owner keeps the lock file open, and Windows refuses to unlink a file
    another process holds. Deleting the lock is therefore the question and the
    answer at once: it fails while the owner lives, and once it succeeds the
    directory is going away anyway.

    A directory with no lock at all was written by an older build; there is
    nothing to ask, so the age check is left to decide on its own. Any other
    error answers "still running", because deleting hundreds of megabytes is
    reversible only by the person who lost them.
    """
    try:
        _session_lock_path(directory).unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return False


def _remove_abandoned_history_directories(parent: Path) -> None:
    """Delete history directories a killed process never got to clean up.

    Every checkpoint is a whole copy of a document, so an abandoned directory
    can be hundreds of megabytes. Only this module's own directories are
    considered, and only ones no live process still holds — an open session
    that simply has not been edited since yesterday keeps its checkpoints, and
    so keeps its hwp_undo.

    Ownership is the whole test. An unowned directory goes now: waiting adds
    nothing, because nothing is going to come back and claim it. The only age
    the sweep applies is a seconds-wide guard against catching a session
    between creating its directory and writing its lock.
    """
    try:
        entries = list(parent.iterdir())
    except OSError:
        return
    threshold = time.time() - _UNOWNED_HISTORY_GRACE_SECONDS
    for entry in entries:
        if not entry.name.startswith(_HISTORY_DIRECTORY_PREFIX):
            continue
        if entry.name.endswith(_SESSION_LOCK_SUFFIX):
            # A lock whose directory is already gone. Nobody can be holding it
            # for a directory that does not exist, so this only ever removes a
            # few bytes of litter; a held lock refuses and stays.
            if not Path(str(entry)[: -len(_SESSION_LOCK_SUFFIX)]).exists():
                try:
                    entry.unlink()
                except OSError:
                    pass
            continue
        try:
            if not entry.is_dir() or entry.stat().st_mtime > threshold:
                continue
        except OSError:
            continue
        if _session_is_still_running(entry):
            continue
        shutil.rmtree(entry, ignore_errors=True)


@dataclass(frozen=True, slots=True)
class PageControlSnapshot:
    instance_id: str
    control_type: str
    rows: int | None
    columns: int | None

    @property
    def signature(self) -> tuple[str, int | None, int | None]:
        return self.control_type, self.rows, self.columns


@dataclass(frozen=True, slots=True)
class NativeDocumentStructureSnapshot:
    page_count: int
    control_count: int
    control_hash: int


@dataclass(frozen=True, slots=True)
class DocumentEditHistoryEntry:
    document_id: int
    full_name: str
    operation: HistoryOperation
    before: DocumentCheckpoint
    after: DocumentCheckpoint
    page: int | None = None
    before_controls: tuple[PageControlSnapshot, ...] = ()
    after_controls: tuple[PageControlSnapshot, ...] = ()
    # A single-line, unformatted replacement in the main body was observed as
    # exactly one native patch command. Only such an entry may ask the bridge to
    # try one engine Undo before the document-file restore.
    p1_single_text_undo: bool = False
    # Pages observed as affected by the MCP edit. Empty means the operation did
    # not provide a reliable range; undo/redo must not invent one from the
    # page where reopening leaves the caret.
    changed_pages: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class NativeDocumentEditHistoryEntry:
    document_id: int
    full_name: str
    operation: HistoryOperation
    maximum_native_steps: int
    before_page_count: int
    after_page_count: int
    page: int | None = None
    before_controls: tuple[PageControlSnapshot, ...] = ()
    after_controls: tuple[PageControlSnapshot, ...] = ()
    before_document_structure: NativeDocumentStructureSnapshot | None = None
    after_document_structure: NativeDocumentStructureSnapshot | None = None
    # The document fingerprint the native envelope can produce is
    # (page_count, control_count, control_hash) — it carries no body text, so a
    # state that differs only in body text is indistinguishable from the state
    # this entry recorded. These are the recorded page's body text digest, so a
    # later body-only edit on that page makes the fingerprints differ instead of
    # letting a native history walk run straight through it.
    #
    # Scope, stated because it is not the whole document: this is the text of
    # `page` only. A body-only edit on a *different* page still produces an
    # identical pair here. Empty means "not recorded" and is treated as "makes
    # no claim", which is how entries recorded before this field behave.
    before_page_text_signature: str = ""
    after_page_text_signature: str = ""
    # Whole-document TEXT + control signatures from the in-process bridge.
    # These detect body edits on every page without serializing a checkpoint.
    before_content_signature: str = ""
    after_content_signature: str = ""


@dataclass(frozen=True, slots=True)
class LogicalTextPatchHistoryEntry:
    document_id: int
    full_name: str
    operation: Literal["text.patch"]
    original_text: str
    replacement_text: str
    undo_selection: NativeSelection
    redo_selection: NativeSelection
    before_content_signature: str
    after_content_signature: str
    before_page_count: int
    after_page_count: int
    page: int | None = None
    changed_pages: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class LogicalTextPatchBatchHistoryEntry:
    document_id: int
    full_name: str
    operation: Literal["text.patch"]
    forward_commands: tuple[NativeActionCommand, ...]
    inverse_commands: tuple[NativeActionCommand, ...]
    before_content_signature: str
    after_content_signature: str
    before_page_count: int
    after_page_count: int
    page: int | None = None
    changed_pages: tuple[int, ...] = ()


type LiveEditHistoryEntry = (
    DocumentEditHistoryEntry
    | NativeDocumentEditHistoryEntry
    | LogicalTextPatchHistoryEntry
    | LogicalTextPatchBatchHistoryEntry
)


def _document_key(document_id: int, full_name: str) -> tuple[int, str]:
    return document_id, os.path.normcase(os.path.abspath(full_name))


_LOGICAL_HISTORY_TYPES = (
    NativeDocumentEditHistoryEntry,
    LogicalTextPatchHistoryEntry,
    LogicalTextPatchBatchHistoryEntry,
)


def _entry_bytes(entry: LiveEditHistoryEntry) -> int:
    if isinstance(entry, _LOGICAL_HISTORY_TYPES):
        return 0
    total = 0
    for path in _entry_paths(entry):
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _entry_paths(entry: LiveEditHistoryEntry) -> tuple[Path, ...]:
    if isinstance(entry, _LOGICAL_HISTORY_TYPES):
        return ()
    return checkpoint_files(entry.before.path) + checkpoint_files(entry.after.path)


def _entry_retention_paths(entry: LiveEditHistoryEntry) -> tuple[Path, ...]:
    if isinstance(entry, _LOGICAL_HISTORY_TYPES):
        return ()
    return (
        entry.before.path,
        checkpoint_meta_path(entry.before.path),
        entry.after.path,
        checkpoint_meta_path(entry.after.path),
    )


def _entry_recovery_files(
    entry: LiveEditHistoryEntry,
) -> tuple[RecoveryFilePair, ...]:
    if isinstance(entry, _LOGICAL_HISTORY_TYPES):
        return ()
    return (
        _checkpoint_recovery_files(entry.before.path),
        _checkpoint_recovery_files(entry.after.path),
    )


def build_capture_document_commands(
    checkpoint_path: Path,
) -> tuple[NativeActionCommand, ...]:
    if not checkpoint_path.is_absolute():
        raise ValueError("document checkpoint path must be absolute")
    return (SaveDocumentFileCommand(checkpoint_path),)


@final
class LiveEditHistoryStore:
    __slots__ = (
        "_max_bytes",
        "_max_checkpoint_files",
        "_max_entries",
        "_redo",
        "_recovery_expiry_stop",
        "_recovery_expiry_thread",
        "_recovery_retention",
        "_session_lock",
        "_temp_parent",
        "_temporary_directory",
        "_total_bytes",
        "_undo",
    )

    def __init__(
        self,
        *,
        temp_parent: Path | None = None,
        max_entries: int = 20,
        max_bytes: int = MAX_HISTORY_DISK_BYTES,
        max_checkpoint_files: int = 3,
    ) -> None:
        if max_entries < 1 or max_bytes < 1 or max_checkpoint_files < 1:
            raise ValueError("history limits must be positive")
        self._max_entries = max_entries
        self._max_checkpoint_files = max_checkpoint_files
        self._max_bytes = max_bytes
        self._temp_parent = temp_parent
        self._temporary_directory: TemporaryDirectory[str] | None = None
        self._session_lock: IO[bytes] | None = None
        self._undo: list[LiveEditHistoryEntry] = []
        self._redo: list[LiveEditHistoryEntry] = []
        self._recovery_retention = RollbackRecoveryRetention()
        self._recovery_expiry_stop = Event()
        self._recovery_expiry_thread: Thread | None = None
        self._total_bytes = 0
        _remove_abandoned_history_directories(
            self._temp_parent
            if self._temp_parent is not None
            else Path(tempfile.gettempdir())
        )

    def _root(self) -> Path:
        if self._temporary_directory is None:
            _remove_abandoned_history_directories(
                self._temp_parent
                if self._temp_parent is not None
                else Path(tempfile.gettempdir())
            )
            self._temporary_directory = TemporaryDirectory(
                prefix=_HISTORY_DIRECTORY_PREFIX,
                dir=self._temp_parent,
            )
            self._hold_session_lock(Path(self._temporary_directory.name))
        return Path(self._temporary_directory.name)

    def _hold_session_lock(self, directory: Path) -> None:
        """Claim the directory for as long as this process is alive.

        The handle stays open on purpose; it is the whole mechanism. The pid it
        carries is for a person reading the temp folder, not for the sweep. A
        failure to open it is not worth failing an edit over — the directory
        then simply looks like one an older build wrote, and the age check
        decides alone.
        """
        path = _session_lock_path(directory)
        try:
            self._session_lock = path.open("wb")
            _ = self._session_lock.write(str(os.getpid()).encode("ascii"))
            self._session_lock.flush()
        except OSError:
            self._session_lock = None

    def _release_session_lock(self) -> None:
        lock, self._session_lock = self._session_lock, None
        if lock is None:
            return
        name = getattr(lock, "name", "")
        try:
            lock.close()
        except OSError:
            pass
        if not name:
            return
        try:
            Path(str(name)).unlink(missing_ok=True)
        except OSError:
            pass

    def new_checkpoint_path(self) -> Path:
        return self._root() / f"document-{secrets.token_hex(12)}.hwp-checkpoint"

    @staticmethod
    def _matches(
        entry: LiveEditHistoryEntry,
        document_id: int,
        full_name: str,
    ) -> bool:
        return _document_key(entry.document_id, entry.full_name) == _document_key(
            document_id,
            full_name,
        )

    def available(
        self,
        direction: HistoryDirection,
        document_id: int,
        full_name: str,
    ) -> int:
        stack = self._undo if direction == "undo" else self._redo
        count = 0
        for entry in reversed(stack):
            if not self._matches(entry, document_id, full_name):
                break
            count += 1
        return count

    def peek(
        self,
        direction: HistoryDirection,
        document_id: int,
        full_name: str,
    ) -> LiveEditHistoryEntry | None:
        stack = self._undo if direction == "undo" else self._redo
        if not stack:
            return None
        entry = stack[-1]
        if not self._matches(entry, document_id, full_name):
            raise HwpLiveError("다른 문서의 편집 이력이 현재 세션에 남아 있습니다")
        return entry

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _drop_entry(self, entry: LiveEditHistoryEntry) -> None:
        self._recovery_retention.discover(_entry_recovery_files(entry))
        for path in _entry_retention_paths(entry):
            self._remove_file(path)
        self._refresh_total_bytes()

    def _refresh_total_bytes(self) -> None:
        entries = (*self._undo, *self._redo)
        recovery_root = (
            Path(self._temporary_directory.name)
            if self._temporary_directory is not None
            else None
        )
        if recovery_root is not None:
            self._recovery_retention.discover_directory(recovery_root)
        for entry in entries:
            self._recovery_retention.discover(_entry_recovery_files(entry))
        self._recovery_retention.expire()
        checkpoint_bytes = 0
        for entry in entries:
            for path in _entry_retention_paths(entry):
                try:
                    checkpoint_bytes += path.stat().st_size
                except OSError:
                    continue
        recovery_bytes = self._recovery_retention.total_bytes()
        self._total_bytes = checkpoint_bytes + recovery_bytes
        if recovery_root is not None and recovery_bytes:
            self._start_recovery_expiry(recovery_root)

    @staticmethod
    def _expire_recovery_until_stopped(directory: Path, stop: Event) -> None:
        while not stop.wait(_ROLLBACK_RECOVERY_SWEEP_SECONDS):
            if not directory.exists():
                return
            retention = RollbackRecoveryRetention()
            retention.discover_directory(directory)
            retention.expire()

    def _start_recovery_expiry(self, directory: Path) -> None:
        current = self._recovery_expiry_thread
        if current is not None and current.is_alive():
            return
        self._recovery_expiry_stop.clear()
        thread = Thread(
            target=self._expire_recovery_until_stopped,
            args=(directory, self._recovery_expiry_stop),
            name=f"HancomRollbackExpiry-{directory.name}",
            daemon=True,
        )
        self._recovery_expiry_thread = thread
        thread.start()

    def _stop_recovery_expiry(self) -> None:
        thread, self._recovery_expiry_thread = self._recovery_expiry_thread, None
        if thread is None:
            return
        self._recovery_expiry_stop.set()
        thread.join()

    def _clear_stack(self, stack: list[LiveEditHistoryEntry]) -> None:
        while stack:
            self._drop_entry(stack.pop())

    def validate_capacity(self, entry: LiveEditHistoryEntry) -> None:
        if isinstance(entry, LogicalTextPatchBatchHistoryEntry):
            if entry.before_page_count < 1 or entry.after_page_count < 1:
                raise ValueError(
                    "logical text patch batch page counts must be positive"
                )
            if not entry.forward_commands or not entry.inverse_commands:
                raise ValueError(
                    "logical text patch batch requires forward and inverse commands"
                )
            return
        if isinstance(entry, LogicalTextPatchHistoryEntry):
            if entry.before_page_count < 1 or entry.after_page_count < 1:
                raise ValueError("logical text patch page counts must be positive")
            if not all(
                checkpoint_signature_is_complete(signature)
                for signature in (
                    entry.before_content_signature,
                    entry.after_content_signature,
                )
            ):
                raise ValueError("logical text patch requires both content signatures")
            if entry.original_text == entry.replacement_text:
                raise ValueError("logical text patch must change text")
            for selection in (entry.undo_selection, entry.redo_selection):
                if (
                    not selection.selected
                    or selection.start.list_id != selection.end.list_id
                    or selection.start.paragraph != selection.end.paragraph
                ):
                    raise ValueError(
                        "logical text patch requires one-list, one-paragraph ranges"
                    )
            return
        if isinstance(entry, NativeDocumentEditHistoryEntry):
            if (
                entry.maximum_native_steps < 1
                or entry.maximum_native_steps > MAX_NATIVE_HISTORY_STEPS
            ):
                raise ValueError(
                    "grouped native history steps must be between 1 and 100"
                )
            if entry.before_page_count < 1 or entry.after_page_count < 1:
                raise ValueError("grouped native history page counts must be positive")
            if entry.operation == "control.delete":
                if entry.page is None or not entry.before_controls:
                    raise ValueError(
                        "control delete history requires its page and controls"
                    )
            elif entry.operation == "document.delete_page" and entry.page is None:
                raise ValueError("page delete history requires its deleted page")
            elif entry.operation == "text.patch":
                raise ValueError("text patch history requires document checkpoints")
            return
        if entry.before.page_count < 1 or entry.after.page_count < 1:
            raise ValueError("document checkpoint page counts must be positive")
        if entry.before.path == entry.after.path:
            raise ValueError("before and after checkpoints must use different files")
        if entry.operation == "control.delete":
            if entry.page is None or not entry.before_controls:
                raise ValueError(
                    "control delete history requires its page and controls"
                )
            if len(entry.before_controls) > 100:
                raise HwpLiveError("한 번의 삭제 복구 대상은 최대 100개입니다")
        elif entry.operation == "document.delete_page" and entry.page is None:
            raise ValueError("page delete history requires its deleted page")
        required = _entry_bytes(entry) + max(entry.before.bytes, entry.after.bytes)
        if required > self._max_bytes:
            raise HwpLiveError(
                "문서 체크포인트가 세션의 디스크 이력 한도를 초과했습니다"
            )
        for checkpoint in (entry.before, entry.after):
            try:
                actual = checkpoint.path.stat().st_size
            except OSError as error:
                raise HwpLiveError(
                    "문서 체크포인트 파일을 확인하지 못했습니다"
                ) from error
            if actual < 1 or actual != checkpoint.bytes:
                raise HwpLiveError("문서 체크포인트 파일 크기가 일치하지 않습니다")

    @staticmethod
    def _existing_file_count(entry: LiveEditHistoryEntry) -> int:
        return sum(path.exists() for path in _entry_retention_paths(entry))

    def _oldest_file_entry(
        self,
    ) -> tuple[list[LiveEditHistoryEntry], int] | None:
        for stack in (self._undo, self._redo):
            for index, entry in enumerate(stack):
                if self._existing_file_count(entry):
                    return stack, index
        return None

    def enforce_limits(self) -> None:
        entries = (*self._undo, *self._redo)
        self._refresh_total_bytes()
        file_count = sum(self._existing_file_count(entry) for entry in entries)
        while (
            self._total_bytes > self._max_bytes
            or file_count > self._max_checkpoint_files
        ):
            oldest = self._oldest_file_entry()
            if oldest is None:
                break
            stack, index = oldest
            self._drop_entry(stack.pop(index))
            entries = (*self._undo, *self._redo)
            file_count = sum(self._existing_file_count(entry) for entry in entries)

    def record(self, entry: LiveEditHistoryEntry) -> None:
        self.validate_capacity(entry)
        self._clear_stack(self._redo)
        self._undo.append(entry)
        self._refresh_total_bytes()
        while len(self._undo) > self._max_entries:
            self._drop_entry(self._undo.pop(0))
        self.enforce_limits()

    def commit(
        self,
        direction: HistoryDirection,
        entry: LiveEditHistoryEntry,
    ) -> None:
        source = self._undo if direction == "undo" else self._redo
        target = self._redo if direction == "undo" else self._undo
        if not source or source[-1] != entry:
            raise HwpLiveError("문서 편집 이력이 실행 중 변경되었습니다")
        _ = source.pop()
        target.append(entry)

    def drop(
        self,
        direction: HistoryDirection,
        entry: LiveEditHistoryEntry,
    ) -> None:
        """Remove `entry` without moving it onto the opposite stack.

        Used when the engine's own history — not this store — is what actually
        moved the document. Keeping the entry would offer a later restore whose
        checkpoint no longer describes any state the engine can reach, so the
        opposite stack is cleared for the same reason: once one link of the
        checkpoint chain is untrustworthy, the rest of it is too.
        """
        source = self._undo if direction == "undo" else self._redo
        if not source or source[-1] != entry:
            raise HwpLiveError("문서 편집 이력이 실행 중 변경되었습니다")
        self._drop_entry(source.pop())
        self._clear_stack(self._redo if direction == "undo" else self._undo)

    def discard(self, entry: LiveEditHistoryEntry) -> None:
        for path in _entry_retention_paths(entry):
            self._remove_file(path)
        recovery_files = _entry_recovery_files(entry)
        self._recovery_retention.discover(recovery_files)
        self._recovery_retention.discard(recovery_files)
        self._refresh_total_bytes()

    def cleanup(self) -> None:
        self._stop_recovery_expiry()
        self._undo.clear()
        self._redo.clear()
        self._recovery_retention.clear()
        self._total_bytes = 0
        # Nothing owns the directory once this returns, so the claim goes with
        # it. Doing this first also means the lock file is gone before the
        # directory it names, never after.
        self._release_session_lock()
        temporary_directory, self._temporary_directory = self._temporary_directory, None
        if temporary_directory is not None:
            temporary_directory.cleanup()
