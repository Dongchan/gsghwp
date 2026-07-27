from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import cast, final
from unittest.mock import patch

import anyio
import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_live_bridge_contract import (  # noqa: E402
    HancomDialogDismissResult,
    HancomWindowState,
    HancomWindowStateList,
)
from hwp_live_contract import (  # noqa: E402
    ConnectedDocument,
    MutationResult,
    OpenDocument,
    OpenDocumentList,
)
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_process_lane import (  # noqa: E402
    HwpLaneOperationContext,
    current_lane_operation_context,
)
from hwp_mcp_dispatch import McpThreadDispatcher  # noqa: E402
from hwp_mcp_operation_executor import HwpOperationExecutor  # noqa: E402
from hwp_operation_contract import HwpOperateInputs, OperationResult  # noqa: E402
from hwp_operation_idempotency import (  # noqa: E402
    OperationIdempotency,
    OperationTicket,
)
from hwp_operation_journal import OperationJournal  # noqa: E402
from hwp_save_fingerprint import (  # noqa: E402
    SaveFileFingerprint,
    capture_save_file_fingerprint,
    reconcile_save_fingerprint,
)
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_session_lifecycle import (  # noqa: E402
    SaveStateMachine,
    save_preflight_result,
)
from hwp_live_session_types import LiveSessionReference  # noqa: E402
from hwp_live_windows import WindowStateReader  # noqa: E402
from hwp_public_contract import PublicActionResult, to_public_action_result  # noqa: E402


@final
class _Signal:
    def start(self, process_id: int, moniker_name: str | None = None) -> None:
        _ = process_id, moniker_name

    def sequence(self) -> int:
        return 0

    def wait(self, after_sequence: int, timeout_seconds: float) -> int:
        _ = after_sequence, timeout_seconds
        return 0

    def stop(self) -> None:
        return


@final
class _WindowReader:
    def __init__(self, process_by_handle: dict[int, int]) -> None:
        self._process_by_handle = process_by_handle

    def read(self, window_handle: int) -> HancomWindowState:
        return HancomWindowState(
            window_handle=window_handle,
            process_id=self._process_by_handle[window_handle],
            exists=True,
            visible=True,
            enabled=True,
            foreground=True,
            title="fixture.hwp - 한글",
            class_name="HwpMain",
            dialogs=(),
        )

    def list_visible_hwp_windows(self) -> HancomWindowStateList:
        return HancomWindowStateList(
            windows=tuple(self.read(handle) for handle in self._process_by_handle)
        )

    def dismiss_dialogs(self, window_handle: int) -> HancomDialogDismissResult:
        _ = window_handle
        raise AssertionError("tests never dismiss user dialogs")


@final
class _DiscoveryController:
    def __init__(self, document: OpenDocument) -> None:
        self._document = document
        self.block_listing = False
        self.list_entered = Event()
        self.release_listing = Event()
        self.list_calls = 0

    def list_open_documents(self) -> OpenDocumentList:
        self.list_calls += 1
        if self.block_listing:
            self.list_entered.set()
            _ = self.release_listing.wait(1)
        return OpenDocumentList(documents=(self._document,))

    def current_session(self) -> None:
        return None

    def restore_activation(self) -> None:
        return

    def close(self) -> None:
        return


@final
class _ProcessController:
    def __init__(self, document: OpenDocument) -> None:
        self._document = document
        self._session_id: str | None = None
        self.disconnect_calls: list[str] = []
        self.operate_entered = Event()
        self.release_operate = Event()

    def connect_deferred(self, selector: str) -> ConnectedDocument:
        assert selector == self._document.selector
        self._session_id = "target-session"
        return ConnectedDocument(
            session_id=self._session_id,
            document=self._document,
        )

    def current_session(self) -> LiveSessionReference | None:
        if self._session_id is None:
            return None
        return LiveSessionReference(
            session_id=self._session_id,
            selector=self._document.selector,
        )

    def session_for_selector(self, selector: str) -> LiveSessionReference | None:
        current = self.current_session()
        return current if current is not None and current.selector == selector else None

    def connection_moniker(self, session_id: str) -> str:
        assert session_id == self._session_id
        return "!HancomLiveBridge.fixture"

    def disconnect(self, session_id: str) -> MutationResult:
        assert session_id == self._session_id
        self.disconnect_calls.append(session_id)
        self._session_id = None
        return MutationResult(action="disconnect", current_page=1, modified=False)

    def set_style_state_token(self, session_id: str, state_token: str) -> None:
        assert session_id == self._session_id
        _ = state_token

    def operate(
        self,
        session_id: str,
        *_args: object,
        **_kwargs: object,
    ) -> OperationResult:
        assert session_id == self._session_id
        self.operate_entered.set()
        assert self.release_operate.wait(1)
        assert (
            save_preflight_result(
                "save",
                resolve_only=False,
                allow_document_change=True,
            )
            is None
        )
        context = current_lane_operation_context()
        assert context is not None
        state = context.save_state()
        assert isinstance(state, SaveStateMachine)
        state.native_returned(source="native_return")
        state.metadata_changed(source="file_metadata")
        state.verified(source="native_verification")
        return OperationResult(
            status="executed",
            query="save",
            registry_entries=1,
            lookup_microseconds=0,
            message="saved",
            verified=True,
            modified=False,
            retry_safe=True,
        )

    def last_operation_routing_context(self, session_id: str) -> None:
        assert session_id == self._session_id
        return None

    def restore_activation(self) -> None:
        return

    def close(self) -> None:
        return


def test_known_pid_connection_skips_blocked_global_document_enumeration() -> None:
    document = OpenDocument(
        selector="pid-202-document-1",
        title="fixture.hwp",
        full_name=r"C:\fixtures\fixture.hwp",
        document_id=1,
        format="HWP",
        edit_mode=1,
        modified=False,
        page_count=1,
        active=True,
        window_handle=20201,
    )
    discovery = _DiscoveryController(document)
    process = _ProcessController(document)
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, discovery)),
        window_reader=cast(
            WindowStateReader,
            cast(object, _WindowReader({document.window_handle: 202})),
        ),
        change_signal=_Signal(),
        controller_factory=lambda: cast(
            LiveHwpController,
            cast(object, process),
        ),
        call_timeout_seconds=1,
    )

    try:
        first = bridge.ensure_connection(document.selector)
        assert first.session_id == "target-session"
        assert discovery.list_calls == 1
        discovery.block_listing = True

        with ThreadPoolExecutor(max_workers=2) as calls:
            blocked_discovery = calls.submit(bridge.list_open_documents)
            assert discovery.list_entered.wait(1)
            repeated = calls.submit(bridge.ensure_connection, document.selector)
            try:
                assert repeated.result(timeout=0.1).session_id == "target-session"
            finally:
                discovery.release_listing.set()
                assert blocked_discovery.result(timeout=1).documents == (document,)
                _ = repeated.result(timeout=1)

        assert discovery.list_calls == 2
    finally:
        discovery.release_listing.set()
        bridge.close()


def _save_document(path: Path) -> OpenDocument:
    return OpenDocument(
        selector="save-document",
        title=path.name,
        full_name=str(path),
        document_id=7,
        format="HWP",
        edit_mode=1,
        modified=True,
        page_count=1,
        active=True,
        window_handle=7001,
    )


_SAVE_BEFORE_BYTES = b"before executor save"
_SAVE_AFTER_BYTES = b"after executor save with changed bytes"
_WINDOWS_FILETIME_EPOCH = 116_444_736_000_000_000


@final
class _SaveEvidenceBridge:
    def __init__(self, path: Path, violation: str | None) -> None:
        self.path = path
        self.violation = violation
        self.document = _save_document(path)
        self.ensure_calls: list[tuple[str | None, bool | None]] = []
        self.operate_calls: list[tuple[str, str, dict[str, object]]] = []
        self.raw_result: OperationResult | None = None
        self.after_stat: os.stat_result | None = None

    def ensure_connection(
        self,
        selector: str | None,
        *,
        persistent: bool | None = None,
    ) -> ConnectedDocument:
        self.ensure_calls.append((selector, persistent))
        assert selector == str(self.path)
        return ConnectedDocument(
            session_id="save-evidence-session",
            document=self.document,
        )

    def operate(
        self,
        session_id: str,
        intent: str,
        _parameters: object,
        **kwargs: object,
    ) -> OperationResult:
        self.operate_calls.append((session_id, intent, kwargs))
        after_bytes = (
            _SAVE_BEFORE_BYTES if self.violation == "sha" else _SAVE_AFTER_BYTES
        )
        _ = self.path.write_bytes(after_bytes)
        current = self.path.stat()
        changed_mtime_ns = current.st_mtime_ns + 5_000_000_000
        os.utime(
            self.path,
            ns=(changed_mtime_ns, changed_mtime_ns),
        )
        after_stat = self.path.stat()
        self.after_stat = after_stat
        actual_filetime = after_stat.st_mtime_ns // 100 + _WINDOWS_FILETIME_EPOCH
        result = OperationResult(
            status="executed",
            changed=True,
            query=intent,
            registry_entries=1,
            lookup_microseconds=0,
            message="native save returned",
            verification="native_save_result",
            verified=True,
            modified=False,
            saved_path=(
                str(self.path.with_name("wrong-target.hwp"))
                if self.violation == "path"
                else str(self.path)
            ),
            before_modified=True,
            save_hresult=(-2_147_467_259 if self.violation == "hresult" else 0),
            save_return=0 if self.violation == "return" else 1,
            post_save_modified=self.violation == "modified",
            saved_file_size=(
                after_stat.st_size + 1
                if self.violation == "size"
                else after_stat.st_size
            ),
            saved_file_write_time_100ns=(
                actual_filetime + 1 if self.violation == "mtime" else actual_filetime
            ),
            live_state_preserved_after_save=True,
            disk_persistence_verified=False,
            partial_mutation=False,
            retry_safe=True,
            reconcile_required=False,
        )
        self.raw_result = result
        return result


def _execute_public_save_case(
    tmp_path: Path,
    violation: str | None,
) -> tuple[
    PublicActionResult,
    OperationResult,
    _SaveEvidenceBridge,
    os.stat_result,
    os.stat_result,
]:
    path = tmp_path / f"executor-{violation or 'success'}.hwp"
    _ = path.write_bytes(_SAVE_BEFORE_BYTES)
    baseline_mtime_ns = path.stat().st_mtime_ns
    os.utime(path, ns=(baseline_mtime_ns, baseline_mtime_ns))
    before_stat = path.stat()
    bridge = _SaveEvidenceBridge(path, violation)
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(
        cast(HancomBridge, cast(object, bridge)),
        dispatcher,
        OperationJournal(tmp_path / f"journal-{violation or 'success'}"),
    )
    inputs = HwpOperateInputs(
        request_id=f"save-evidence-{violation or 'success'}",
        document=str(path),
        operation="document.save",
    )

    async def execute() -> OperationResult:
        try:
            return await executor.execute("save", inputs, None)
        finally:
            await dispatcher.close(lambda: None)

    result = anyio.run(execute)
    public = to_public_action_result(result, ())
    assert bridge.raw_result is not None
    assert bridge.after_stat is not None
    return public, result, bridge, before_stat, bridge.after_stat


def _save_proof_conditions(
    path: Path,
    raw: OperationResult,
    before_sha256: str,
    after_stat: os.stat_result,
) -> dict[str, bool]:
    return {
        "path": raw.saved_path == str(path),
        "hresult": raw.save_hresult is not None and raw.save_hresult >= 0,
        "return": raw.save_return == 1,
        "modified": raw.post_save_modified is False,
        "size": raw.saved_file_size == after_stat.st_size,
        "mtime": raw.saved_file_write_time_100ns
        == after_stat.st_mtime_ns // 100 + _WINDOWS_FILETIME_EPOCH,
        "sha": hashlib.sha256(path.read_bytes()).hexdigest() != before_sha256,
    }


def test_executor_save_builds_public_evidence_from_real_file_readback(
    tmp_path: Path,
) -> None:
    public, result, bridge, before_stat, after_stat = _execute_public_save_case(
        tmp_path,
        None,
    )
    path = bridge.path
    raw = bridge.raw_result
    assert raw is not None
    before_sha256 = hashlib.sha256(_SAVE_BEFORE_BYTES).hexdigest()
    after_sha256 = hashlib.sha256(_SAVE_AFTER_BYTES).hexdigest()

    assert bridge.ensure_calls == [(str(path), None)]
    assert len(bridge.operate_calls) == 1
    session_id, intent, kwargs = bridge.operate_calls[0]
    assert session_id == "save-evidence-session"
    assert intent == "save"
    assert kwargs["workflow"] == "document.save"
    assert kwargs["resolve_only"] is False
    assert kwargs["allow_document_change"] is True
    assert all(_save_proof_conditions(path, raw, before_sha256, after_stat).values())
    assert raw.save_baseline_sha256 is None
    assert raw.saved_file_sha256 is None
    assert raw.save_fingerprint_verified is None
    assert raw.disk_persistence_verified is False

    assert result.status == "executed"
    assert result.idempotency_status == "committed"
    assert public.status == "succeeded"
    assert public.verified is True
    assert public.save_evidence is not None
    evidence = public.save_evidence
    assert evidence.path == str(path)
    assert evidence.save_hresult == 0
    assert evidence.save_return == 1
    assert evidence.post_save_modified is False
    assert evidence.baseline_file_size == before_stat.st_size
    assert evidence.baseline_file_mtime_ns == before_stat.st_mtime_ns
    assert evidence.baseline_sha256 == before_sha256
    assert evidence.file_size == after_stat.st_size
    assert evidence.file_write_time_100ns == (
        after_stat.st_mtime_ns // 100 + _WINDOWS_FILETIME_EPOCH
    )
    assert evidence.file_mtime_ns == after_stat.st_mtime_ns
    assert evidence.sha256 == after_sha256
    assert evidence.fingerprint_stable is True
    assert evidence.fingerprint_changed is True
    assert evidence.fingerprint_verified is True
    assert evidence.disk_persistence_verified is True


@pytest.mark.parametrize(
    ("violation", "expected_status", "expected_changed"),
    (
        ("path", "partial_failure", True),
        ("hresult", "partial_failure", True),
        ("return", "partial_failure", True),
        ("modified", "partial_failure", True),
        ("size", "partial_failure", True),
        ("mtime", "partial_failure", True),
        ("sha", "failed", False),
    ),
)
def test_executor_public_save_evidence_rejects_each_broken_proof_condition(
    tmp_path: Path,
    violation: str,
    expected_status: str,
    expected_changed: bool,
) -> None:
    public, result, bridge, _before_stat, after_stat = _execute_public_save_case(
        tmp_path,
        violation,
    )
    raw = bridge.raw_result
    assert raw is not None
    conditions = _save_proof_conditions(
        bridge.path,
        raw,
        hashlib.sha256(_SAVE_BEFORE_BYTES).hexdigest(),
        after_stat,
    )

    assert {name for name, satisfied in conditions.items() if not satisfied} == {
        violation
    }
    assert result.idempotency_status == "failed"
    assert public.status == expected_status
    assert public.verified is False
    assert public.save_evidence is not None
    assert public.save_evidence.fingerprint_stable is True
    assert public.save_evidence.fingerprint_changed is expected_changed
    assert public.save_evidence.fingerprint_verified is False
    assert public.save_evidence.disk_persistence_verified is False


def _failed_save_result(
    path: Path,
    *,
    native_completion: bool,
) -> OperationResult:
    stat = path.stat()
    return OperationResult(
        status="transport_error",
        changed=True,
        query="save",
        registry_entries=1,
        lookup_microseconds=0,
        message="save response was lost",
        verified=False,
        saved_path=str(path),
        before_modified=True,
        save_hresult=0 if native_completion else None,
        save_return=1 if native_completion else None,
        post_save_modified=False if native_completion else None,
        saved_file_size=stat.st_size if native_completion else None,
        saved_file_write_time_100ns=(
            stat.st_mtime_ns // 100 + 116_444_736_000_000_000
            if native_completion
            else None
        ),
        partial_mutation=True,
        retry_safe=False,
        reconcile_required=True,
    )


def _successful_save_result(path: Path) -> OperationResult:
    stat = path.stat()
    return OperationResult(
        status="executed",
        changed=True,
        query="save",
        registry_entries=1,
        lookup_microseconds=0,
        message="saved",
        verification="native_save_result",
        verified=True,
        modified=False,
        saved_path=str(path),
        before_modified=True,
        save_hresult=0,
        save_return=1,
        post_save_modified=False,
        saved_file_size=stat.st_size,
        saved_file_write_time_100ns=(stat.st_mtime_ns // 100 + 116_444_736_000_000_000),
        live_state_preserved_after_save=True,
        disk_persistence_verified=False,
        partial_mutation=False,
        retry_safe=True,
        reconcile_required=False,
    )


def _successful_save_reopen_result(path: Path) -> OperationResult:
    return OperationResult(
        status="executed",
        changed=True,
        query="save and reopen",
        registry_entries=1,
        lookup_microseconds=0,
        message="saved and reopened",
        verification="native_save_reopen_result",
        verified=True,
        modified=False,
        reopened_path=str(path),
        before_modified=True,
        save_hresult=0,
        save_return=1,
        post_save_modified=False,
        live_state_preserved_after_save=True,
        disk_persistence_verified=True,
        partial_mutation=False,
        retry_safe=True,
        reconcile_required=False,
    )


def test_save_confirmation_requires_stable_before_and_after_hashes() -> None:
    path = r"C:\documents\stable-save.hwp"
    after_mtime_ns = 1_000_000_000
    result = OperationResult(
        status="transport_error",
        query="save",
        registry_entries=1,
        lookup_microseconds=0,
        message="save response was lost",
        verified=False,
        saved_path=path,
        before_modified=False,
        save_hresult=0,
        save_return=0,
        post_save_modified=False,
        saved_file_size=12,
        saved_file_write_time_100ns=(after_mtime_ns // 100 + 116_444_736_000_000_000),
        retry_safe=False,
        reconcile_required=True,
    )

    reconciled, disposition = reconcile_save_fingerprint(
        result,
        before=SaveFileFingerprint(
            path=path,
            exists=True,
            size=12,
            mtime_ns=900_000_000,
            sha256="1" * 64,
            stable=False,
        ),
        after=SaveFileFingerprint(
            path=path,
            exists=True,
            size=12,
            mtime_ns=after_mtime_ns,
            sha256="1" * 64,
            stable=True,
        ),
    )

    assert disposition == "unavailable"
    assert reconciled.verified is False
    assert reconciled.save_fingerprint_stable is False
    assert reconciled.save_fingerprint_verified is False
    assert reconciled.disk_persistence_verified is False


def test_missing_save_baseline_reason_is_attached_without_relaxing_verdict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "created-by-save.hwp"
    document = _save_document(path)
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "missing-journal"))
    prepared = idempotency.prepare(
        document,
        "save",
        HwpOperateInputs(
            request_id="missing-save-baseline",
            document=document.full_name,
            operation="document.save",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)
    assert prepared.save_fingerprint_before is not None
    assert prepared.save_fingerprint_before.capture_status == "file_missing"

    _ = path.write_bytes(b"created by native save")
    result = idempotency.commit(prepared, _successful_save_result(path))

    assert result.failure_stage == "save_baseline_file_missing"
    assert "[save_baseline_reason=file_missing]" in result.message
    assert result.status == "partial_change"
    assert result.verified is False
    assert result.save_fingerprint_verified is False
    assert result.disk_persistence_verified is False


def test_inaccessible_save_baseline_reason_is_attached_without_relaxing_verdict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inaccessible-before-save.hwp"
    _ = path.write_bytes(b"before inaccessible save")
    document = _save_document(path)
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "access-journal"))
    with patch(
        "hwp_save_fingerprint.Path.open",
        side_effect=PermissionError("sharing violation"),
    ):
        inaccessible = capture_save_file_fingerprint(str(path))
    with patch(
        "hwp_operation_idempotency.capture_save_file_fingerprint",
        return_value=inaccessible,
    ):
        prepared = idempotency.prepare(
            document,
            "save",
            HwpOperateInputs(
                request_id="inaccessible-save-baseline",
                document=document.full_name,
                operation="document.save",
            ),
            None,
        )
    assert isinstance(prepared, OperationTicket)
    assert prepared.save_fingerprint_before is not None
    assert prepared.save_fingerprint_before.capture_status == "access_failed"

    _ = path.write_bytes(b"after inaccessible save")
    result = idempotency.commit(prepared, _successful_save_result(path))

    assert result.failure_stage == "save_baseline_access_failed"
    assert "[save_baseline_reason=access_failed]" in result.message
    assert result.status == "operation_failed"
    assert result.verified is False
    assert result.save_fingerprint_verified is False
    assert result.disk_persistence_verified is False


def test_failed_save_attaches_captured_baseline_without_relaxing_verdict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "failed-with-baseline.hwp"
    before_bytes = b"before failed save"
    _ = path.write_bytes(before_bytes)
    before_stat = path.stat()
    document = _save_document(path)
    idempotency = OperationIdempotency(
        OperationJournal(tmp_path / "failed-with-baseline-journal")
    )
    prepared = idempotency.prepare(
        document,
        "save",
        HwpOperateInputs(
            request_id="failed-save-with-baseline",
            document=document.full_name,
            operation="document.save",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)

    _ = path.write_bytes(b"native save may have changed the file")
    with patch(
        "hwp_operation_idempotency.capture_save_file_fingerprint",
        side_effect=AssertionError("failed commit must not read the file again"),
    ) as capture_after:
        result = idempotency.commit(
            prepared,
            _failed_save_result(path, native_completion=True),
        )
    capture_after.assert_not_called()
    public = to_public_action_result(result, ())

    assert result.save_baseline_file_size == before_stat.st_size
    assert result.save_baseline_file_mtime_ns == before_stat.st_mtime_ns
    assert result.save_baseline_sha256 == hashlib.sha256(before_bytes).hexdigest()
    assert result.failure_stage is None
    assert "save_baseline_reason=not_attached" not in result.message
    assert result.status == "transport_error"
    assert result.idempotency_status == "failed"
    assert result.verified is False
    assert result.retry_safe is False
    assert result.reconcile_required is True
    assert result.save_fingerprint_verified is None
    assert result.disk_persistence_verified is None
    assert public.status == "partial_failure"
    assert public.save_evidence is not None
    assert public.save_evidence.baseline_file_size == before_stat.st_size
    assert public.save_evidence.baseline_file_mtime_ns == before_stat.st_mtime_ns
    assert (
        public.save_evidence.baseline_sha256 == hashlib.sha256(before_bytes).hexdigest()
    )
    assert public.save_evidence.fingerprint_verified is None

    recorded = idempotency.status(document, "failed-save-with-baseline")
    assert recorded.status == "operation_failed"
    assert recorded.idempotency_status == "failed"
    assert recorded.verified is False
    assert recorded.reconcile_required is True
    assert recorded.save_baseline_file_size == before_stat.st_size
    assert recorded.save_baseline_file_mtime_ns == before_stat.st_mtime_ns
    assert recorded.save_baseline_sha256 == hashlib.sha256(before_bytes).hexdigest()


@pytest.mark.parametrize(
    ("fingerprint", "reason"),
    (
        (
            SaveFileFingerprint(
                path=r"C:\documents\missing.hwp",
                exists=False,
                stable=True,
                capture_status="file_missing",
            ),
            "file_missing",
        ),
        (
            SaveFileFingerprint(
                path=r"C:\documents\inaccessible.hwp",
                exists=None,
                stable=False,
                capture_status="access_failed",
            ),
            "access_failed",
        ),
        (
            SaveFileFingerprint(
                path=r"C:\documents\unstable.hwp",
                exists=True,
                size=20,
                mtime_ns=123_000_000,
                sha256="a" * 64,
                stable=False,
                capture_status="unstable",
            ),
            "unstable",
        ),
        (
            SaveFileFingerprint(
                path=r"C:\documents\incomplete.hwp",
                exists=True,
                stable=True,
            ),
            "incomplete",
        ),
    ),
)
def test_failed_save_preserves_baseline_capture_failure_reason(
    tmp_path: Path,
    fingerprint: SaveFileFingerprint,
    reason: str,
) -> None:
    path = tmp_path / f"failed-{reason}.hwp"
    _ = path.write_bytes(b"save failure fixture")
    document = _save_document(path)
    idempotency = OperationIdempotency(
        OperationJournal(tmp_path / f"failed-{reason}-journal")
    )
    with patch(
        "hwp_operation_idempotency.capture_save_file_fingerprint",
        return_value=fingerprint,
    ):
        prepared = idempotency.prepare(
            document,
            "save",
            HwpOperateInputs(
                request_id=f"failed-save-{reason}",
                document=document.full_name,
                operation="document.save",
            ),
            None,
        )
    assert isinstance(prepared, OperationTicket)

    result = idempotency.commit(
        prepared,
        _failed_save_result(path, native_completion=False),
    )

    assert result.failure_stage == f"save_baseline_{reason}"
    assert f"[save_baseline_reason={reason}]" in result.message
    assert "save_baseline_reason=not_attached" not in result.message
    assert result.save_baseline_file_size == fingerprint.size
    assert result.save_baseline_file_mtime_ns == fingerprint.mtime_ns
    assert result.save_baseline_sha256 == fingerprint.sha256
    assert result.status == "transport_error"
    assert result.idempotency_status == "failed"
    assert result.verified is False
    assert result.reconcile_required is True
    assert result.save_fingerprint_verified is None


def test_capture_status_does_not_add_file_reads_or_change_complete_fingerprint(
    tmp_path: Path,
) -> None:
    path = tmp_path / "complete-fingerprint.hwp"
    contents = b"complete fingerprint"
    _ = path.write_bytes(contents)

    fingerprint = capture_save_file_fingerprint(str(path))

    assert fingerprint.capture_status == "complete"
    assert fingerprint.exists is True
    assert fingerprint.size == len(contents)
    assert fingerprint.sha256 == hashlib.sha256(contents).hexdigest()
    assert fingerprint.stable is True


def test_legacy_journal_without_capture_status_remains_queryable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-journal-save.hwp"
    contents = b"legacy journal baseline"
    _ = path.write_bytes(contents)
    before = capture_save_file_fingerprint(str(path))
    root = tmp_path / "legacy-journal"
    journal = OperationJournal(root)
    document_session = "legacy-save-document-session"
    operation_id = "legacy-save-without-capture-status"
    decision = journal.begin(
        document_session,
        operation_id,
        "legacy-request-digest",
        document_path=str(path),
        operation="document.save",
        save_fingerprint_before=before,
    )
    assert decision.kind == "execute"
    journal.mark_executing(document_session, operation_id)
    journal.mark_failed(
        document_session,
        operation_id,
        "native_response_lost",
        result=_failed_save_result(path, native_completion=False),
    )
    record_path = next(root.glob("*.json"))
    payload = cast(
        dict[str, object], json.loads(record_path.read_text(encoding="utf-8"))
    )
    baseline = cast(dict[str, object], payload["save_fingerprint_before"])
    assert baseline.pop("capture_status") == "complete"
    _ = record_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    legacy_journal = OperationJournal(root)
    legacy_decision = legacy_journal.lookup(document_session, operation_id)
    assert legacy_decision is not None
    assert legacy_decision.save_fingerprint_before is not None
    assert legacy_decision.save_fingerprint_before.capture_status is None

    result = OperationIdempotency(legacy_journal).status_without_connection(
        operation_id,
        str(path),
    )

    assert result is not None
    assert result.status == "transport_error"
    assert result.idempotency_status == "failed"
    assert result.verified is False
    assert result.save_baseline_file_size == len(contents)
    assert result.save_baseline_sha256 == hashlib.sha256(contents).hexdigest()
    assert result.save_fingerprint_verified is False


def test_successful_save_commit_attaches_before_and_after_fingerprints(
    tmp_path: Path,
) -> None:
    path = tmp_path / "successful.hwp"
    before_bytes = b"before successful save"
    after_bytes = b"after successful save with different content"
    _ = path.write_bytes(before_bytes)
    before_stat = path.stat()
    document = _save_document(path)
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "success-journal"))
    prepared = idempotency.prepare(
        document,
        "save",
        HwpOperateInputs(
            request_id="successful-save",
            document=document.full_name,
            operation="document.save",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)

    _ = path.write_bytes(after_bytes)
    after_stat = path.stat()
    result = idempotency.commit(prepared, _successful_save_result(path))
    public = to_public_action_result(result, ())

    assert result.status == "executed"
    assert result.idempotency_status == "committed"
    assert result.failure_stage is None
    assert result.verified is True
    assert result.save_fingerprint_verified is True
    assert result.disk_persistence_verified is True
    assert public.save_evidence is not None
    evidence = public.save_evidence
    assert evidence.baseline_file_size == before_stat.st_size
    assert evidence.baseline_file_mtime_ns == before_stat.st_mtime_ns
    assert evidence.baseline_sha256 == hashlib.sha256(before_bytes).hexdigest()
    assert evidence.file_size == after_stat.st_size
    assert evidence.file_write_time_100ns == (
        after_stat.st_mtime_ns // 100 + 116_444_736_000_000_000
    )
    assert evidence.file_mtime_ns == after_stat.st_mtime_ns
    assert evidence.sha256 == hashlib.sha256(after_bytes).hexdigest()
    assert evidence.fingerprint_stable is True
    assert evidence.fingerprint_changed is True
    assert evidence.fingerprint_verified is True
    assert evidence.disk_persistence_verified is True


def test_successful_requestless_save_still_attaches_fingerprints(
    tmp_path: Path,
) -> None:
    path = tmp_path / "requestless.hwp"
    before_bytes = b"before requestless save"
    after_bytes = b"after requestless save"
    _ = path.write_bytes(before_bytes)
    document = _save_document(path)
    idempotency = OperationIdempotency(
        OperationJournal(tmp_path / "requestless-journal")
    )
    prepared = idempotency.prepare(
        document,
        "save",
        HwpOperateInputs(
            document=document.full_name,
            operation="document.save",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)

    _ = path.write_bytes(after_bytes)
    result = idempotency.commit(prepared, _successful_save_result(path))

    assert result.idempotency_status == "not_requested"
    assert result.request_id is None
    assert result.save_baseline_sha256 == hashlib.sha256(before_bytes).hexdigest()
    assert result.saved_file_sha256 == hashlib.sha256(after_bytes).hexdigest()
    assert result.save_fingerprint_verified is True
    assert result.disk_persistence_verified is True


def test_requestless_save_without_changed_hash_is_not_reported_as_success(
    tmp_path: Path,
) -> None:
    path = tmp_path / "requestless-unchanged.hwp"
    _ = path.write_bytes(b"unchanged requestless save")
    document = _save_document(path)
    idempotency = OperationIdempotency(
        OperationJournal(tmp_path / "requestless-unchanged-journal")
    )
    prepared = idempotency.prepare(
        document,
        "save",
        HwpOperateInputs(
            document=document.full_name,
            operation="document.save",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)

    result = idempotency.commit(prepared, _successful_save_result(path))

    assert result.status == "operation_failed"
    assert result.verified is False
    assert result.save_fingerprint_changed is False
    assert result.save_fingerprint_verified is False
    assert result.disk_persistence_verified is False
    assert result.retry_safe is False
    assert result.reconcile_required is True


def test_successful_save_reopen_attaches_fingerprints_without_weakening_rules(
    tmp_path: Path,
) -> None:
    path = tmp_path / "save-reopen.hwp"
    before_bytes = b"before save reopen"
    after_bytes = b"after save reopen"
    _ = path.write_bytes(before_bytes)
    before_stat = path.stat()
    document = _save_document(path)
    idempotency = OperationIdempotency(OperationJournal(tmp_path / "reopen-journal"))
    prepared = idempotency.prepare(
        document,
        "save and reopen",
        HwpOperateInputs(
            request_id="successful-save-reopen",
            document=document.full_name,
            operation="document.save_reopen_verify",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)

    _ = path.write_bytes(after_bytes)
    after_stat = path.stat()
    result = idempotency.commit(prepared, _successful_save_reopen_result(path))

    assert result.status == "executed"
    assert result.idempotency_status == "committed"
    assert result.save_baseline_file_size == before_stat.st_size
    assert result.save_baseline_file_mtime_ns == before_stat.st_mtime_ns
    assert result.save_baseline_sha256 == hashlib.sha256(before_bytes).hexdigest()
    assert result.saved_file_size == after_stat.st_size
    assert result.saved_file_mtime_ns == after_stat.st_mtime_ns
    assert result.saved_file_sha256 == hashlib.sha256(after_bytes).hexdigest()
    assert result.save_fingerprint_stable is True
    assert result.save_fingerprint_changed is True
    assert result.save_fingerprint_verified is False
    assert result.disk_persistence_verified is True


def _record_failed_save(
    journal: OperationJournal,
    document: OpenDocument,
    operation_id: str,
    *,
    native_completion: bool,
) -> None:
    idempotency = OperationIdempotency(journal)
    prepared = idempotency.prepare(
        document,
        "save",
        HwpOperateInputs(
            request_id=operation_id,
            document=document.full_name,
            operation="document.save",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)
    path = Path(document.full_name)
    _ = path.write_bytes(b"saved document bytes")
    _ = idempotency.commit(
        prepared,
        _failed_save_result(path, native_completion=native_completion),
    )


def test_failed_save_is_immediately_reconciled_from_fingerprint(
    tmp_path: Path,
) -> None:
    path = tmp_path / "immediate.hwp"
    _ = path.write_bytes(b"before save")
    document = _save_document(path)
    operation_id = "immediate-save-reconciliation"
    journal = OperationJournal(tmp_path / "immediate-journal")
    idempotency = OperationIdempotency(journal)
    prepared = idempotency.prepare(
        document,
        "save",
        HwpOperateInputs(
            request_id=operation_id,
            document=document.full_name,
            operation="document.save",
        ),
        None,
    )
    assert isinstance(prepared, OperationTicket)
    _ = path.write_bytes(b"saved document bytes")
    failed = idempotency.commit(
        prepared,
        _failed_save_result(path, native_completion=True),
    )
    bridge = HancomBridge(LiveHwpController())
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(bridge, dispatcher, journal)
    reconcile_now = cast(
        Callable[[OperationTicket | None, OperationResult, str], OperationResult],
        getattr(executor, "_immediate_save_reconciliation"),
    )

    try:
        with patch.object(
            HancomBridge,
            "reconcile_confirmed_save",
            return_value=True,
        ) as reconcile:
            result = reconcile_now(prepared, failed, document.selector)
    finally:

        async def close_dispatcher() -> None:
            await dispatcher.close(lambda: None)

        anyio.run(close_dispatcher)

    reconcile.assert_called_once_with(document.selector, operation_id)
    assert result.status == "executed"
    assert result.verified is True
    assert result.reconcile_required is False
    assert result.save_fingerprint_verified is True
    assert result.saved_file_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_operation_status_confirms_saved_file_without_com_connection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "confirmed.hwp"
    _ = path.write_bytes(b"before save")
    document = _save_document(path)
    operation_id = "confirmed-save-without-com"
    journal = OperationJournal(tmp_path / "confirmed-journal")
    _record_failed_save(
        journal,
        document,
        operation_id,
        native_completion=True,
    )
    bridge = HancomBridge(LiveHwpController())
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(bridge, dispatcher, journal)

    async def query() -> OperationResult:
        try:
            return await executor.get_operation_status(operation_id, None)
        finally:
            await dispatcher.close(lambda: None)

    with (
        patch.object(
            HancomBridge,
            "ensure_connection",
            side_effect=HwpLiveError("COM connection is unavailable"),
        ) as ensure,
        patch.object(
            HancomBridge,
            "reconcile_confirmed_save",
            return_value=True,
        ) as reconcile,
    ):
        result = anyio.run(query)

    assert ensure.call_count == 0
    reconcile.assert_called_once_with(str(path), operation_id)
    assert result.status == "executed"
    assert result.verified is True
    assert result.retry_safe is True
    assert result.reconcile_required is False
    evidence = result.model_dump()
    current = path.stat()
    assert evidence["saved_file_size"] == current.st_size
    assert evidence["saved_file_mtime_ns"] == current.st_mtime_ns
    assert (
        evidence["saved_file_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert evidence["save_fingerprint_stable"] is True
    assert evidence["save_fingerprint_changed"] is True
    assert evidence["save_fingerprint_verified"] is True
    assert evidence["disk_persistence_verified"] is True

    replayed = OperationIdempotency(journal).status_without_connection(
        operation_id,
        str(path),
    )
    assert replayed is not None
    assert replayed.idempotency_status == "replayed"
    assert replayed.save_baseline_sha256 == hashlib.sha256(b"before save").hexdigest()
    assert replayed.saved_file_sha256 == evidence["saved_file_sha256"]
    assert replayed.save_fingerprint_verified is True
    assert replayed.disk_persistence_verified is True


def test_changed_fingerprint_without_native_completion_is_not_called_success(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unconfirmed.hwp"
    _ = path.write_bytes(b"before save")
    document = _save_document(path)
    operation_id = "unconfirmed-save-without-com"
    journal = OperationJournal(tmp_path / "unconfirmed-journal")
    _record_failed_save(
        journal,
        document,
        operation_id,
        native_completion=False,
    )
    bridge = HancomBridge(LiveHwpController())
    dispatcher = McpThreadDispatcher(watch_workers=1)
    executor = HwpOperationExecutor(bridge, dispatcher, journal)

    async def query() -> OperationResult:
        try:
            return await executor.get_operation_status(operation_id, str(path))
        finally:
            await dispatcher.close(lambda: None)

    with (
        patch.object(
            HancomBridge,
            "ensure_connection",
            side_effect=HwpLiveError("COM connection is unavailable"),
        ) as ensure,
        patch.object(
            HancomBridge,
            "reconcile_confirmed_save",
            return_value=True,
        ) as reconcile,
    ):
        result = anyio.run(query)

    public = to_public_action_result(result, ())
    assert ensure.call_count == 0
    assert reconcile.call_count == 0
    assert result.status == "partial_change"
    assert result.verified is False
    assert result.retry_safe is False
    assert result.reconcile_required is True
    assert "저장된 것으로 보이나 미확정" in result.message
    assert public.status == "partial_failure"
    assert public.save_evidence is not None
    assert public.save_evidence.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert public.save_evidence.fingerprint_stable is True
    assert public.save_evidence.fingerprint_changed is True
    assert public.save_evidence.fingerprint_verified is False
    assert public.save_evidence.disk_persistence_verified is False


def test_sessionless_disconnect_cannot_overtake_save_state_attachment(
    tmp_path: Path,
) -> None:
    path = tmp_path / "race.hwp"
    _ = path.write_bytes(b"fixture")
    document = _save_document(path).model_copy(
        update={
            "selector": "pid-303-document-1",
            "window_handle": 30301,
        }
    )
    discovery = _DiscoveryController(document)
    process = _ProcessController(document)
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, discovery)),
        window_reader=cast(
            WindowStateReader,
            cast(object, _WindowReader({document.window_handle: 303})),
        ),
        change_signal=_Signal(),
        controller_factory=lambda: cast(
            LiveHwpController,
            cast(object, process),
        ),
        call_timeout_seconds=1,
    )
    _ = bridge.ensure_connection(document.selector)
    close_guard_called = Event()
    original_guard = HwpLaneOperationContext.request_close_guard

    def traced_guard(
        context: HwpLaneOperationContext,
    ) -> object:
        close_guard_called.set()
        return original_guard(context)

    try:
        with (
            patch.object(
                HwpLaneOperationContext,
                "request_close_guard",
                traced_guard,
            ),
            ThreadPoolExecutor(max_workers=2) as calls,
        ):
            saving = calls.submit(
                bridge.operate,
                "target-session",
                "save",
                {},
                resolve_only=False,
                allow_document_change=True,
                use_defaults=False,
                expected_cursor=None,
                workflow="document.save",
            )
            assert process.operate_entered.wait(1)
            disconnecting = calls.submit(bridge.disconnect)
            assert close_guard_called.wait(1)
            process.release_operate.set()

            saved = saving.result(timeout=1)
            assert saved.status == "executed"
            with pytest.raises(HwpLiveError) as blocked:
                _ = disconnecting.result(timeout=1)

        assert "MCP 자동 연결 해제만 보류" in blocked.value.reason
        assert "한글 UI의 저장·닫기 조작은 차단하지 않습니다" in blocked.value.reason
        assert process.disconnect_calls == []

        disconnected = bridge.disconnect()
        assert disconnected.action == "disconnect"
        assert process.disconnect_calls == ["target-session"]
    finally:
        process.release_operate.set()
        bridge.close()
