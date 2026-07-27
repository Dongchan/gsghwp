from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from gc import collect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast, final
from unittest.mock import patch
from weakref import ReferenceType, ref

import pytest
from pydantic import TypeAdapter


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_native_batch import (  # noqa: E402
    NativeActivationCallError,
    _batch_for_window,
    _invalidate_native_dispatch,
    _native_dispatch_state,
    _source_for_window,
    _Win32Client,
    activate_native_document,
    clear_native_document_route,
    execute_native_actions,
    inspect_native_pages,
    native_dispatch_cache_metrics,
    native_dispatch_operation_scope,
    read_native_snapshot,
    reset_native_dispatch_cache_metrics,
    select_native_document_route,
)
from hwp_live_rot import (  # noqa: E402
    HwpDocumentCandidate,
    HwpRotCatalog,
    _addon_document_id,
    _addon_window_handle,
    activate_candidate,
    restore_active_document,
)
from hwp_live_api import HwpComApplication, HwpComDocument  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_action_models import NativeActionRequest  # noqa: E402


@final
class _Moniker:
    def __init__(self, name: str) -> None:
        self.name = name

    def GetDisplayName(self, context: object, moniker: object) -> str:
        _ = (context, moniker)
        return self.name


@final
class _Source:
    def __init__(self, name: str) -> None:
        self.name = name

    def QueryInterface(self, interface_id: object) -> object:
        _ = interface_id
        return self


@final
class _Rot:
    def __init__(self, names: tuple[str, ...]) -> None:
        self.enum_calls = 0
        self.monikers = tuple(_Moniker(name) for name in names)
        self.sources = {
            moniker.name: _Source(moniker.name) for moniker in self.monikers
        }

    def EnumRunning(self) -> tuple[_Moniker, ...]:
        self.enum_calls += 1
        return self.monikers

    def GetObject(self, moniker: object) -> _Source:
        assert isinstance(moniker, _Moniker)
        return self.sources[moniker.name]


@final
class _PythonCom:
    IID_IDispatch = object()

    def __init__(self, rot: _Rot) -> None:
        self.rot = rot

    def CoInitialize(self) -> None:
        pass

    def CoUninitialize(self) -> None:
        pass

    def CreateBindCtx(self, reserved: int) -> object:
        _ = reserved
        return object()

    def GetRunningObjectTable(self) -> _Rot:
        return self.rot


@final
class _Process:
    def GetWindowThreadProcessId(self, window_handle: int) -> tuple[int, int]:
        _ = window_handle
        return 7, 1234


@final
class _MutableProcess:
    def __init__(self, process_id: int) -> None:
        self.process_id = process_id

    def GetWindowThreadProcessId(self, window_handle: int) -> tuple[int, int]:
        _ = window_handle
        return 7, self.process_id


@final
class _UnexpectedProcess:
    def __init__(self) -> None:
        self.fail = False

    def GetWindowThreadProcessId(self, window_handle: int) -> tuple[int, int]:
        _ = window_handle
        if self.fail:
            raise LookupError("unexpected PID lookup failure")
        return 7, 1234


@final
class _DynamicBatch:
    def __init__(
        self,
        *,
        protocol_version: int = 12,
        target_document_id: int = 0,
    ) -> None:
        self._protocol_version = protocol_version
        self._target_document_id = target_document_id
        self.protocol_version_reads = 0
        self.target_document_id_reads = 0
        self.activated_document_id = 0
        self.fail_snapshot = False
        self.missing_inspect_pages = False
        self.snapshot_calls = 0

    @property
    def ProtocolVersion(self) -> int:
        self.protocol_version_reads += 1
        return self._protocol_version

    @property
    def TargetDocumentID(self) -> int:
        self.target_document_id_reads += 1
        return self._target_document_id

    def ActivateDocument(self, document_id: int) -> str:
        self.activated_document_id = document_id
        return "Local\\HancomLiveActivation.test"

    def ActivationStatus(self, document_id: int) -> int:
        return 1 if document_id == self.activated_document_id else -1

    def Execute(self, payload: str) -> str:
        _ = payload
        return "{}"

    def ExecuteActions(self, payload: str) -> str:
        _ = payload
        return "{}"

    def Snapshot(self) -> str:
        self.snapshot_calls += 1
        if self.fail_snapshot:
            raise RuntimeError("disconnected native batch")
        return "{}"

    def InspectPage(self, page: int) -> str:
        _ = page
        return "{}"

    def InspectPageV3(self, page: int) -> str:
        _ = page
        return "{}"

    def InspectPageSummary(self, page: int) -> str:
        _ = page
        return "{}"

    def InspectPagesV3(self, pages: str) -> str:
        _ = pages
        if self.missing_inspect_pages:
            raise AttributeError("InspectPagesV3")
        return "{}"

    def InspectRoutingContext(self, page_hint: int) -> str:
        _ = page_hint
        return "{}"

    def InspectStructure(self, page: int) -> str:
        _ = page
        return "{}"

    def ProbeOfficialApi(self, payload: str) -> str:
        _ = payload
        return "{}"

    def SaveReopenVerify(self) -> str:
        return "{}"

    def SaveVerify(self) -> str:
        return "{}"


@final
class _EventHandle:
    def Close(self) -> None:
        return


@final
class _Events:
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258

    def OpenEvent(
        self,
        desired_access: int,
        inherit_handle: bool,
        name: str,
    ) -> _EventHandle:
        _ = desired_access, inherit_handle, name
        return _EventHandle()

    def WaitForMultipleObjects(
        self,
        handles: tuple[_EventHandle, ...],
        wait_all: bool,
        milliseconds: int,
    ) -> int:
        _ = handles, wait_all, milliseconds
        return self.WAIT_OBJECT_0


@final
class _BatchClient:
    def __init__(self, batch: _DynamicBatch) -> None:
        self.batch = batch
        self.dispatch_calls = 0
        self.dispatched_sources: list[object] = []

    def Dispatch(self, source: object) -> _DynamicBatch:
        self.dispatch_calls += 1
        self.dispatched_sources.append(source)
        return self.batch


@final
class _EphemeralBatchClient:
    def __init__(self) -> None:
        self.dispatch_calls = 0
        self.dispatch_refs: list[ReferenceType[_DynamicBatch]] = []

    def Dispatch(self, source: object) -> _DynamicBatch:
        _ = source
        self.dispatch_calls += 1
        batch = _DynamicBatch()
        self.dispatch_refs.append(ref(batch))
        return batch


@final
class _CatalogDocument:
    def __init__(self, documents: _CatalogDocuments, document_id: int) -> None:
        self._documents = documents
        self.DocumentID = document_id
        self.FullName = f"C:/docs/{document_id}.hwp"
        self.Format = "HWP"
        self.EditMode = 1
        self.Modified = 0

    def SetActive_XHwpDocument(self) -> None:
        self._documents.active = self


@final
class _CatalogDocuments:
    def __init__(
        self,
        *,
        missing_active_reads: int = 0,
        missing_item_reads: int = 0,
        always_missing_active: bool = False,
    ) -> None:
        self.items = (
            _CatalogDocument(self, 1),
            _CatalogDocument(self, 2),
        )
        self.active = self.items[0]
        self.active_reads = 0
        self.item_reads = 0
        self.missing_active_reads = missing_active_reads
        self.missing_item_reads = missing_item_reads
        self.always_missing_active = always_missing_active

    @property
    def Count(self) -> int:
        return len(self.items)

    @property
    def Active_XHwpDocument(self) -> _CatalogDocument | None:
        self.active_reads += 1
        if self.always_missing_active or self.active_reads <= self.missing_active_reads:
            return None
        return self.active

    def Item(self, index: int) -> _CatalogDocument | None:
        self.item_reads += 1
        if self.item_reads <= self.missing_item_reads:
            return None
        return self.items[index]


@final
class _CatalogWindow:
    WindowHandle = 5678


@final
class _CatalogWindows:
    Active_XHwpWindow = _CatalogWindow()


@final
class _CatalogApplication:
    def __init__(
        self,
        *,
        missing_active_reads: int = 0,
        missing_item_reads: int = 0,
        always_missing_active: bool = False,
    ) -> None:
        self.XHwpDocuments = _CatalogDocuments(
            missing_active_reads=missing_active_reads,
            missing_item_reads=missing_item_reads,
            always_missing_active=always_missing_active,
        )
        self.XHwpWindows = _CatalogWindows()

    @property
    def PageCount(self) -> int:
        return self.XHwpDocuments.active.DocumentID + 1


@final
class _CatalogClient:
    def __init__(self, application: _CatalogApplication) -> None:
        self.application = application

    def Dispatch(self, source: object) -> _CatalogApplication:
        assert isinstance(source, _Source)
        document_id = _addon_document_id(source.name)
        if document_id is not None:
            self.application.XHwpDocuments.active = (
                self.application.XHwpDocuments.items[document_id - 1]
            )
        return self.application


@final
class _MissingDocumentsApplication:
    @property
    def XHwpDocuments(self) -> _CatalogDocuments:
        raise AttributeError("<unknown>.XHwpDocuments")


@final
class _MissingDocumentsClient:
    def Dispatch(self, source: object) -> _MissingDocumentsApplication:
        assert isinstance(source, _Source)
        return _MissingDocumentsApplication()


def test_native_batch_prefers_exact_process_window_moniker() -> None:
    clear_native_document_route(5678)
    rot = _Rot(
        (
            "!HancomLiveBatch.1234",
            "!HancomLiveBatch.1234.5678",
        )
    )

    source = _source_for_window(5678, _PythonCom(rot), _Process())

    assert source is rot.sources["!HancomLiveBatch.1234.5678"]


def test_native_batch_reuses_dispatch_for_the_same_short_lived_route() -> None:
    clear_native_document_route(5678)
    reset_native_dispatch_cache_metrics()
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _Process()

    first = _batch_for_window(5678, 12, pythoncom, client, process)
    second = _batch_for_window(5678, 12, pythoncom, client, process)

    assert first is batch
    assert second is batch
    assert client.dispatch_calls == 1
    metrics = native_dispatch_cache_metrics()
    assert metrics.cache_misses == 1
    assert metrics.cache_hits == 1
    assert metrics.rot_scans == 1
    assert metrics.dispatch_creations == 1
    assert metrics.lookup_nanoseconds > 0
    assert batch.protocol_version_reads == 1
    assert batch.target_document_id_reads == 2


def test_native_batch_cache_does_not_strongly_own_the_dispatch() -> None:
    clear_native_document_route(5678)
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    client = _BatchClient(batch)

    selected = _batch_for_window(
        5678,
        12,
        _PythonCom(rot),
        client,
        _Process(),
    )

    assert selected is batch
    entry = next(
        entry for key, entry in _native_dispatch_state.entries.items() if key[1] == 5678
    )
    assert not hasattr(entry, "batch")
    assert entry.batch_ref() is batch


def test_native_batch_operation_scope_keeps_dispatch_after_first_call() -> None:
    clear_native_document_route(5678)
    reset_native_dispatch_cache_metrics()
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    client = _EphemeralBatchClient()
    pythoncom = _PythonCom(rot)
    process = _Process()

    with native_dispatch_operation_scope():
        first = _batch_for_window(5678, 12, pythoncom, client, process)
        assert first is not None
        first_ref = ref(first)
        first = None
        _ = collect()

        second = _batch_for_window(5678, 12, pythoncom, client, process)

        assert second is first_ref()
        assert client.dispatch_calls == 1
        metrics = native_dispatch_cache_metrics()
        assert metrics.cache_misses == 1
        assert metrics.cache_hits == 1
        assert metrics.rot_scans == 1

    second = None
    _ = collect()
    assert first_ref() is None


def test_native_batch_operation_scope_caches_dispatch_properties() -> None:
    clear_native_document_route(5678)
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _Process()

    with native_dispatch_operation_scope():
        for _ in range(5):
            selected = _batch_for_window(5678, 12, pythoncom, client, process)
            assert selected is batch
            assert batch.Snapshot() == "{}"

    assert batch.snapshot_calls == 5
    assert batch.protocol_version_reads == 1
    assert batch.target_document_id_reads == 1


def test_native_batch_target_document_mismatch_is_still_blocked() -> None:
    rot = _Rot(("!HancomLiveBatch.1234.5678.42",))
    batch = _DynamicBatch(target_document_id=43)
    client = _BatchClient(batch)
    select_native_document_route(5678, 42)
    try:
        with (
            native_dispatch_operation_scope(),
            pytest.raises(HwpLiveError, match="문서 ID가 대상과 다릅니다"),
        ):
            _ = _batch_for_window(
                5678,
                12,
                _PythonCom(rot),
                client,
                _Process(),
            )
    finally:
        clear_native_document_route(5678)

    assert client.dispatch_calls == 1
    assert batch.protocol_version_reads == 1
    assert batch.target_document_id_reads == 1


def test_native_batch_invalidation_rereads_dispatch_properties() -> None:
    clear_native_document_route(5678)
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _Process()

    with native_dispatch_operation_scope():
        first = _batch_for_window(5678, 12, pythoncom, client, process)
        _invalidate_native_dispatch(5678)
        second = _batch_for_window(5678, 12, pythoncom, client, process)

    assert first is batch
    assert second is batch
    assert client.dispatch_calls == 2
    assert batch.protocol_version_reads == 2
    assert batch.target_document_id_reads == 2


def test_native_batch_target_cache_ends_with_operation_scope() -> None:
    clear_native_document_route(5678)
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _Process()

    with native_dispatch_operation_scope():
        _ = _batch_for_window(5678, 12, pythoncom, client, process)
        _ = _batch_for_window(5678, 12, pythoncom, client, process)

    assert not any(key[1] == 5678 for key in _native_dispatch_state.operation_entries)

    with native_dispatch_operation_scope():
        _ = _batch_for_window(5678, 12, pythoncom, client, process)

    assert batch.protocol_version_reads == 1
    assert batch.target_document_id_reads == 2


def test_nested_native_batch_scope_keeps_outer_target_cache() -> None:
    clear_native_document_route(5678)
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _Process()

    with native_dispatch_operation_scope():
        first = _batch_for_window(5678, 12, pythoncom, client, process)
        with native_dispatch_operation_scope():
            second = _batch_for_window(5678, 12, pythoncom, client, process)
        third = _batch_for_window(5678, 12, pythoncom, client, process)

        assert any(key[1] == 5678 for key in _native_dispatch_state.operation_entries)

    assert first is batch
    assert second is batch
    assert third is batch
    assert client.dispatch_calls == 1
    assert batch.protocol_version_reads == 1
    assert batch.target_document_id_reads == 1
    assert not any(key[1] == 5678 for key in _native_dispatch_state.operation_entries)


def test_native_batch_route_change_invalidates_cached_dispatch() -> None:
    rot = _Rot(
        (
            "!HancomLiveBatch.1234.5678.41",
            "!HancomLiveBatch.1234.5678.42",
        )
    )
    batch = _DynamicBatch()
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _Process()

    select_native_document_route(5678, 41)
    try:
        with native_dispatch_operation_scope():
            first = _batch_for_window(5678, 12, pythoncom, client, process)
            assert any(
                key[1] == 5678 for key in _native_dispatch_state.operation_entries
            )

            select_native_document_route(5678, 42)

            assert not any(
                key[1] == 5678 for key in _native_dispatch_state.operation_entries
            )
            second = _batch_for_window(5678, 12, pythoncom, client, process)
    finally:
        clear_native_document_route(5678)

    assert first is batch
    assert second is batch
    assert client.dispatch_calls == 2
    assert client.dispatched_sources == [
        rot.sources["!HancomLiveBatch.1234.5678.41"],
        rot.sources["!HancomLiveBatch.1234.5678.42"],
    ]
    assert batch.protocol_version_reads == 2
    assert batch.target_document_id_reads == 2


def test_native_batch_cache_is_scoped_to_the_sta_thread() -> None:
    clear_native_document_route(5678)
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _Process()

    first = _batch_for_window(5678, 12, pythoncom, client, process)
    with ThreadPoolExecutor(max_workers=1) as executor:
        second = executor.submit(
            _batch_for_window,
            5678,
            12,
            pythoncom,
            client,
            process,
        ).result(timeout=1)

    assert first is batch
    assert second is batch
    assert client.dispatch_calls == 2


def test_native_batch_process_change_does_not_reuse_old_dispatch() -> None:
    clear_native_document_route(5678)
    rot = _Rot(
        (
            "!HancomLiveBatch.1234.5678",
            "!HancomLiveBatch.4321.5678",
        )
    )
    batch = _DynamicBatch()
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _MutableProcess(1234)

    first = _batch_for_window(5678, 12, pythoncom, client, process)
    process.process_id = 4321
    second = _batch_for_window(5678, 12, pythoncom, client, process)

    assert first is batch
    assert second is batch
    assert client.dispatch_calls == 2
    assert client.dispatched_sources == [
        rot.sources["!HancomLiveBatch.1234.5678"],
        rot.sources["!HancomLiveBatch.4321.5678"],
    ]


def test_native_batch_dispatch_expires_after_the_short_ttl() -> None:
    clear_native_document_route(5678)
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _Process()

    with patch("hwp_live_native_batch._NATIVE_DISPATCH_CACHE_TTL_SECONDS", 0):
        first = _batch_for_window(5678, 12, pythoncom, client, process)
        second = _batch_for_window(5678, 12, pythoncom, client, process)

    assert first is batch
    assert second is batch
    assert client.dispatch_calls == 2


def test_native_batch_protocol_mismatch_invalidates_new_dispatch() -> None:
    clear_native_document_route(5678)
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    low_batch = _DynamicBatch(protocol_version=11)
    high_batch = _DynamicBatch(protocol_version=12)
    client = _BatchClient(low_batch)
    pythoncom = _PythonCom(rot)
    process = _Process()

    with pytest.raises(HwpLiveError, match="프로토콜 버전"):
        _ = _batch_for_window(5678, 12, pythoncom, client, process)

    client.batch = high_batch
    second = _batch_for_window(5678, 12, pythoncom, client, process)

    assert second is high_batch
    assert client.dispatch_calls == 2
    assert low_batch.protocol_version_reads == 1
    assert low_batch.target_document_id_reads == 0
    assert high_batch.protocol_version_reads == 1
    assert high_batch.target_document_id_reads == 1


def test_native_call_failure_invalidates_cached_dispatch() -> None:
    clear_native_document_route(5678)
    reset_native_dispatch_cache_metrics()
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    batch.fail_snapshot = True
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _Process()

    with (
        patch(
            "hwp_live_native_batch._modules",
            return_value=(pythoncom, client, process),
        ),
        native_dispatch_operation_scope(),
    ):
        with pytest.raises(HwpLiveError, match="현재 상태 조회"):
            _ = read_native_snapshot(5678)

        assert not any(
            key[1] == 5678 for key in _native_dispatch_state.operation_entries
        )

        batch.fail_snapshot = False
        reconnected = _batch_for_window(5678, 12, pythoncom, client, process)
        assert any(key[1] == 5678 for key in _native_dispatch_state.operation_entries)

    metrics = native_dispatch_cache_metrics()

    assert reconnected is batch
    assert client.dispatch_calls == 2
    assert batch.protocol_version_reads == 2
    assert batch.target_document_id_reads == 2
    assert metrics.invalidations == 1


def test_snapshot_decode_failure_invalidates_operation_cache() -> None:
    clear_native_document_route(5678)
    reset_native_dispatch_cache_metrics()
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _Process()

    with (
        patch(
            "hwp_live_native_batch._modules",
            return_value=(pythoncom, client, process),
        ),
        native_dispatch_operation_scope(),
    ):
        with pytest.raises(HwpLiveError, match="현재 상태 응답 형식"):
            _ = read_native_snapshot(5678)

        assert not any(
            key[1] == 5678 for key in _native_dispatch_state.operation_entries
        )
        reconnected = _batch_for_window(5678, 12, pythoncom, client, process)

    metrics = native_dispatch_cache_metrics()
    assert reconnected is batch
    assert batch.snapshot_calls == 1
    assert client.dispatch_calls == 2
    assert batch.protocol_version_reads == 2
    assert batch.target_document_id_reads == 2
    assert metrics.invalidations == 1


def test_action_decode_failure_invalidates_operation_cache() -> None:
    clear_native_document_route(5678)
    reset_native_dispatch_cache_metrics()
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _Process()
    request = cast(NativeActionRequest, object())

    with (
        patch(
            "hwp_live_native_batch._modules",
            return_value=(pythoncom, client, process),
        ),
        patch(
            "hwp_live_native_batch.encode_action_request",
            return_value="encoded action",
        ),
        patch(
            "hwp_live_native_batch.decode_action_result",
            side_effect=HwpLiveError("action decode failure"),
        ),
        native_dispatch_operation_scope(),
    ):
        with pytest.raises(HwpLiveError, match="action decode failure"):
            _ = execute_native_actions(5678, request)

        assert not any(
            key[1] == 5678 for key in _native_dispatch_state.operation_entries
        )
        reconnected = _batch_for_window(5678, 12, pythoncom, client, process)

    metrics = native_dispatch_cache_metrics()
    assert reconnected is batch
    assert client.dispatch_calls == 2
    assert batch.protocol_version_reads == 2
    assert batch.target_document_id_reads == 2
    assert metrics.invalidations == 1


def test_inspect_pages_attribute_fallback_invalidates_operation_cache() -> None:
    clear_native_document_route(5678)
    reset_native_dispatch_cache_metrics()
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    batch.missing_inspect_pages = True
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _Process()
    fallback_page = object()

    with (
        patch(
            "hwp_live_native_batch._modules",
            return_value=(pythoncom, client, process),
        ),
        patch(
            "hwp_live_native_batch.inspect_native_page",
            return_value=fallback_page,
        ) as fallback,
        native_dispatch_operation_scope(),
    ):
        inspected = inspect_native_pages(5678, (1, 2))

        assert inspected == (fallback_page, fallback_page)
        assert fallback.call_count == 2
        assert not any(
            key[1] == 5678 for key in _native_dispatch_state.operation_entries
        )
        batch.missing_inspect_pages = False
        reconnected = _batch_for_window(5678, 12, pythoncom, client, process)

    metrics = native_dispatch_cache_metrics()
    assert reconnected is batch
    assert client.dispatch_calls == 2
    assert batch.protocol_version_reads == 2
    assert batch.target_document_id_reads == 2
    assert metrics.invalidations == 1


def test_unexpected_pid_error_invalidates_operation_cache() -> None:
    clear_native_document_route(5678)
    reset_native_dispatch_cache_metrics()
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    client = _BatchClient(batch)
    pythoncom = _PythonCom(rot)
    process = _UnexpectedProcess()

    with native_dispatch_operation_scope():
        first = _batch_for_window(5678, 12, pythoncom, client, process)
        process.fail = True
        with pytest.raises(LookupError, match="unexpected PID lookup failure"):
            _ = _batch_for_window(5678, 12, pythoncom, client, process)

        assert not any(
            key[1] == 5678 for key in _native_dispatch_state.operation_entries
        )
        process.fail = False
        reconnected = _batch_for_window(5678, 12, pythoncom, client, process)

    metrics = native_dispatch_cache_metrics()
    assert first is batch
    assert reconnected is batch
    assert client.dispatch_calls == 2
    assert batch.protocol_version_reads == 2
    assert batch.target_document_id_reads == 2
    assert metrics.invalidations == 1


def test_rot_catalog_keeps_fallback_documents_missing_scoped_monikers() -> None:
    rot = _Rot(
        (
            "!HancomLiveBridge.1234",
            "!HancomLiveBridge.1234.5678",
            "!HancomLiveBridge.1234.5678.1",
            "!HancomLiveBridge.1234.5678.2",
        )
    )
    application = _CatalogApplication()
    with (
        patch("hwp_live_rot._load_pythoncom", return_value=_PythonCom(rot)),
        patch(
            "hwp_live_rot._load_win32_client",
            return_value=_CatalogClient(application),
        ),
    ):
        catalog = HwpRotCatalog(cache_ttl_seconds=0)
        candidates = catalog.scan(force=True)
        catalog.close()

    assert [candidate.document_id for candidate in candidates] == [1, 2]
    assert candidates[0].moniker_name == "!HancomLiveBridge.1234.5678.1"
    assert candidates[1].moniker_name == "!HancomLiveBridge.1234.5678.2"
    assert [candidate.active for candidate in candidates] == [True, False]
    assert [candidate.page_count for candidate in candidates] == [2, 3]
    assert [candidate.public().page_count for candidate in candidates] == [2, 3]


def test_rot_catalog_skips_missing_item_when_usable_document_exists() -> None:
    rot = _Rot(("!HwpObject.mixed",))
    application = _CatalogApplication(missing_item_reads=1)
    with (
        patch("hwp_live_rot._load_pythoncom", return_value=_PythonCom(rot)),
        patch(
            "hwp_live_rot._load_win32_client",
            return_value=_CatalogClient(application),
        ),
        patch("hwp_live_rot.time.sleep") as sleep,
    ):
        catalog = HwpRotCatalog(cache_ttl_seconds=0)
        candidates = catalog.scan(force=True)
        catalog.close()

    assert [candidate.document_id for candidate in candidates] == [2]
    assert rot.enum_calls == 1
    assert application.XHwpDocuments.item_reads == 2
    sleep.assert_not_called()


def test_rot_catalog_retries_once_when_active_document_is_not_attached() -> None:
    rot = _Rot(("!HwpObject.transient",))
    application = _CatalogApplication(missing_active_reads=1)
    with (
        patch("hwp_live_rot._load_pythoncom", return_value=_PythonCom(rot)),
        patch(
            "hwp_live_rot._load_win32_client",
            return_value=_CatalogClient(application),
        ),
        patch("hwp_live_rot.time.sleep") as sleep,
    ):
        catalog = HwpRotCatalog(cache_ttl_seconds=0)
        candidates = catalog.scan(force=True)
        catalog.close()

    assert [candidate.document_id for candidate in candidates] == [1, 2]
    assert rot.enum_calls == 2
    assert application.XHwpDocuments.active_reads == 2
    sleep.assert_called_once_with(0.02)


def test_rot_catalog_reports_actionable_error_after_bounded_retry() -> None:
    rot = _Rot(("!HwpObject.transient",))
    application = _CatalogApplication(always_missing_active=True)
    with (
        patch("hwp_live_rot._load_pythoncom", return_value=_PythonCom(rot)),
        patch(
            "hwp_live_rot._load_win32_client",
            return_value=_CatalogClient(application),
        ),
        patch("hwp_live_rot.time.sleep") as sleep,
    ):
        catalog = HwpRotCatalog(cache_ttl_seconds=0)
        with pytest.raises(HwpLiveError) as unavailable:
            _ = catalog.scan(force=True)
        catalog.close()

    assert "retry_safe=true" in unavailable.value.reason
    assert "user_action=잠시 후 요청한 읽기 도구를 다시 실행하세요" in (
        unavailable.value.reason
    )
    assert rot.enum_calls == 2
    assert application.XHwpDocuments.active_reads == 2
    sleep.assert_called_once_with(0.02)


def test_rot_catalog_cache_does_not_strongly_own_com_candidates() -> None:
    rot = _Rot(("!HancomLiveBridge.1234.5678.1",))
    application = _CatalogApplication()
    with (
        patch("hwp_live_rot._load_pythoncom", return_value=_PythonCom(rot)),
        patch(
            "hwp_live_rot._load_win32_client",
            return_value=_CatalogClient(application),
        ),
    ):
        catalog = HwpRotCatalog(cache_ttl_seconds=60)
        candidates = catalog.scan(force=True)
        cached = catalog._cached_candidates

    assert cached is not None
    assert all(not isinstance(item, HwpDocumentCandidate) for item in cached)
    assert tuple(item() for item in cached) == candidates
    catalog.close()


def test_rot_catalog_normalizes_unknown_xhwpdocuments_attribute(
    capsys: pytest.CaptureFixture[str],
) -> None:
    moniker = "!HwpObject.unknown"
    rot = _Rot((moniker,))
    with (
        patch("hwp_live_rot._load_pythoncom", return_value=_PythonCom(rot)),
        patch(
            "hwp_live_rot._load_win32_client",
            return_value=_MissingDocumentsClient(),
        ),
    ):
        catalog = HwpRotCatalog(cache_ttl_seconds=0)
        with pytest.raises(HwpLiveError, match="열려 있는 한컴 문서 목록"):
            _ = catalog.scan(force=True)
        catalog.close()

    diagnostic = TypeAdapter(dict[str, str]).validate_json(capsys.readouterr().err)
    assert diagnostic["event"] == "hwp.rot.resolve.error"
    assert diagnostic["moniker"] == moniker
    assert diagnostic["stage"] == "application.XHwpDocuments"
    assert diagnostic["error_type"] == "AttributeError"


def test_native_batch_falls_back_to_legacy_process_moniker() -> None:
    clear_native_document_route(5678)
    rot = _Rot(("!HancomLiveBatch.1234",))

    source = _source_for_window(5678, _PythonCom(rot), _Process())

    assert source is rot.sources["!HancomLiveBatch.1234"]


def test_native_batch_falls_back_when_selected_document_moniker_is_missing() -> None:
    rot = _Rot(
        (
            "!HancomLiveBatch.1234",
            "!HancomLiveBatch.1234.5678",
            "!HancomLiveBatch.1234.5678.42",
        )
    )
    select_native_document_route(5678, 42)
    try:
        source = _source_for_window(5678, _PythonCom(rot), _Process())
        assert source is rot.sources["!HancomLiveBatch.1234.5678.42"]

        missing = _Rot(
            (
                "!HancomLiveBatch.1234",
                "!HancomLiveBatch.1234.5678",
            )
        )
        assert (
            _source_for_window(
                5678,
                _PythonCom(missing),
                _Process(),
            )
            is missing.sources["!HancomLiveBatch.1234.5678"]
        )
    finally:
        clear_native_document_route(5678)


def test_dynamic_window_batch_accepts_selected_document_route() -> None:
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    batch = _DynamicBatch()
    select_native_document_route(5678, 42)
    try:
        selected = _batch_for_window(
            5678,
            12,
            _PythonCom(rot),
            cast(_Win32Client, cast(object, _BatchClient(batch))),
            _Process(),
        )
    finally:
        clear_native_document_route(5678)

    assert selected is batch


def test_native_document_activation_uses_dynamic_window_batch() -> None:
    rot = _Rot(("!HancomLiveBatch.1234.5678",))
    pythoncom = _PythonCom(rot)
    batch = _DynamicBatch()
    client = cast(_Win32Client, cast(object, _BatchClient(batch)))
    with (
        patch(
            "hwp_live_native_batch._modules",
            return_value=(pythoncom, client, _Process()),
        ),
        patch(
            "hwp_live_native_batch.import_module",
            return_value=_Events(),
        ),
    ):
        activated = activate_native_document(5678, 42)

    assert activated
    assert batch.activated_document_id == 42
    clear_native_document_route(5678)


def test_candidate_activation_uses_same_sta_com_fallback_on_native_call_error() -> None:
    application = _CatalogApplication()
    document = application.XHwpDocuments.items[1]
    candidate = HwpDocumentCandidate(
        selector="second",
        moniker_name="!HancomLiveBridge.1234.5678.2",
        application=cast(HwpComApplication, cast(object, application)),
        document=cast(HwpComDocument, cast(object, document)),
        document_id=2,
        full_name=document.FullName,
        document_format=document.Format,
        edit_mode=document.EditMode,
        window_handle=5678,
        active=False,
        page_count=3,
    )

    with patch(
        "hwp_live_rot.activate_native_document",
        side_effect=NativeActivationCallError("native activation unavailable"),
    ):
        activated, restore = activate_candidate(candidate)
        assert application.XHwpDocuments.Active_XHwpDocument is document
        assert activated.active is True
        assert restore is not None
        restore_active_document(restore)

    restored = application.XHwpDocuments.Active_XHwpDocument
    assert restored is not None
    assert restored.DocumentID == 1


def test_addon_window_handle_only_accepts_pid_handle_moniker() -> None:
    assert _addon_window_handle("!HancomLiveBridge.1234.5678") == 5678
    assert _addon_window_handle("!HancomLiveBridge.1234.5678.42") == 5678
    assert _addon_window_handle("!HancomLiveBridge.1234") is None
    assert _addon_window_handle("!HancomLiveBridge.bad.5678") is None
    assert _addon_window_handle("!HwpObject.1234.5678") is None
    assert _addon_document_id("!HancomLiveBridge.1234.5678.42") == 42
    assert _addon_document_id("!HancomLiveBridge.1234.5678") is None
