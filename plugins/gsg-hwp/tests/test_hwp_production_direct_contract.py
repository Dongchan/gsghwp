from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar, cast

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_live_api import HwpComApplication, HwpComDocument  # noqa: E402
from hwp_live_contract import SelectionPosition  # noqa: E402
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate, require_active_candidate  # noqa: E402
from hwp_live_structure_contract import (  # noqa: E402
    FastPageControl,
    FastPageInspection,
    StructurePosition,
)
from hwp_operation_contract import WorkflowTargetCandidate  # noqa: E402
from hwp_public_action_contract import PublicObjectTargetStore  # noqa: E402
from hwp_public_contract import PublicTableTarget  # noqa: E402
from hwp_public_table_target import (  # noqa: E402
    PublicTableTargetStore,
    UnknownPublicTargetError,
)


def _table_inspection(
    *,
    document_id: int,
    full_name: str,
    page: int,
    start: int,
    count: int,
) -> FastPageInspection:
    return FastPageInspection(
        document_id=document_id,
        full_name=full_name,
        page=page,
        page_count=max(page, 3),
        text="",
        controls=tuple(
            FastPageControl(
                control_type="tbl",
                instance_id=f"table-{index}",
                anchor=StructurePosition(
                    list_id=0,
                    paragraph=index,
                    character=0,
                ),
                rows=1,
                columns=1,
            )
            for index in range(start, start + count)
        ),
    )


def test_fast_inspection_raw_id_falls_back_to_direct_resolution_after_clear() -> None:
    # Given
    store = PublicObjectTargetStore()
    inspection = FastPageInspection(
        document_id=17,
        full_name="C:/documents/sample.hwp",
        page=2,
        page_count=3,
        text="",
        controls=(
            FastPageControl(
                control_type="tbl",
                instance_id="native-table-42",
                anchor=StructurePosition(list_id=7, paragraph=3, character=0),
                rows=4,
                columns=2,
            ),
        ),
    )

    # When
    store.remember_inspection(inspection)
    cached = store.resolve_control("native-table-42", None)
    store.clear()
    direct = store.resolve_control("native-table-42", inspection.full_name)

    # Then
    assert cached is not None
    assert cached.document_path == inspection.full_name
    assert cached.target.page_hint == inspection.page
    assert direct is not None
    assert direct.document_path == inspection.full_name
    assert direct.target.page_hint is None
    assert direct.target.control_instance_id == "native-table-42"


def test_opaque_object_ids_remain_document_scoped_and_cache_bound() -> None:
    # Given
    store = PublicObjectTargetStore()
    opaque_id = store.remember(
        (
            WorkflowTargetCandidate(
                kind="picture",
                page=2,
                picture_index=1,
            ),
        ),
        "C:/documents/first.hwp",
    )[0]

    # When
    same_document = store.resolve_picture(
        opaque_id,
        "C:/documents/first.hwp",
    )
    other_document = store.resolve_picture(
        opaque_id,
        "C:/documents/second.hwp",
    )
    store.clear()
    after_clear = store.resolve_picture(opaque_id, "C:/documents/first.hwp")

    # Then
    assert same_document is not None
    assert same_document.document_path == "C:/documents/first.hwp"
    assert other_document is None
    assert after_clear is None


def test_raw_ids_remain_direct_beyond_fast_inspection_cache_bound() -> None:
    # Given
    store = PublicObjectTargetStore()
    controls = tuple(
        FastPageControl(
            control_type="tbl",
            instance_id=f"table-{index}",
            anchor=StructurePosition(list_id=0, paragraph=index, character=0),
            rows=1,
            columns=1,
        )
        for index in range(300)
    )
    inspection = FastPageInspection(
        document_id=17,
        full_name="C:/documents/sample.hwp",
        page=1,
        page_count=3,
        text="",
        controls=controls,
    )

    # When
    store.remember_inspection(inspection)
    cached = store.resolve_control("table-255", None)
    direct = store.resolve_control("table-256", inspection.full_name)

    # Then
    assert cached is not None
    assert cached.document_path == inspection.full_name
    assert cached.target.page_hint == inspection.page
    assert direct is not None
    assert direct.document_path == inspection.full_name
    assert direct.target.page_hint is None
    assert direct.target.control_instance_id == "table-256"


def test_same_document_page_inspections_keep_previous_raw_target_hints() -> None:
    # Given
    table_store = PublicTableTargetStore()
    object_store = PublicObjectTargetStore()
    first = _table_inspection(
        document_id=17,
        full_name=r"C:\Documents\Sample.hwp",
        page=1,
        start=1,
        count=1,
    )
    second = _table_inspection(
        document_id=17,
        full_name=r"c:\documents\sample.hwp",
        page=2,
        start=2,
        count=1,
    )

    # When
    table_store.remember_inspection(first)
    object_store.remember_inspection(first)
    table_store.remember_inspection(second)
    object_store.remember_inspection(second)
    table_target = table_store.resolve(PublicTableTarget(target_id="table-1"))
    object_target = object_store.resolve_control("table-1", None)

    # Then
    assert table_target.document_path == first.full_name
    assert table_target.target.page_hint == 1
    assert object_target is not None
    assert object_target.document_path == first.full_name
    assert object_target.target.page_hint == 1


def test_inspected_target_cache_is_bounded_across_pages() -> None:
    # Given
    table_store = PublicTableTargetStore()
    object_store = PublicObjectTargetStore()
    first = _table_inspection(
        document_id=17,
        full_name="C:/documents/sample.hwp",
        page=1,
        start=0,
        count=200,
    )
    second = _table_inspection(
        document_id=17,
        full_name="C:/documents/sample.hwp",
        page=2,
        start=200,
        count=100,
    )

    # When
    table_store.remember_inspection(first)
    object_store.remember_inspection(first)
    table_store.remember_inspection(second)
    object_store.remember_inspection(second)

    # Then
    assert (
        table_store.resolve(PublicTableTarget(target_id="table-43")).target.page_hint
        is None
    )
    assert (
        table_store.resolve(PublicTableTarget(target_id="table-44")).target.page_hint
        == 1
    )
    assert (
        table_store.resolve(PublicTableTarget(target_id="table-299")).target.page_hint
        == 2
    )
    evicted_object = object_store.resolve_control(
        "table-43",
        "C:/documents/sample.hwp",
    )
    retained_object = object_store.resolve_control("table-44", None)
    newest_object = object_store.resolve_control("table-299", None)
    assert evicted_object is not None
    assert evicted_object.target.page_hint is None
    assert retained_object is not None
    assert retained_object.target.page_hint == 1
    assert newest_object is not None
    assert newest_object.target.page_hint == 2


def test_document_transition_invalidates_raw_hints_and_opaque_targets() -> None:
    # Given
    table_store = PublicTableTargetStore()
    object_store = PublicObjectTargetStore()
    table_opaque = table_store.remember(
        (WorkflowTargetCandidate(kind="table", page=1, table_index=1),),
        "C:/documents/first.hwp",
    )[0]
    object_opaque = object_store.remember(
        (WorkflowTargetCandidate(kind="picture", page=1, picture_index=1),),
        "C:/documents/first.hwp",
    )[0]
    first = _table_inspection(
        document_id=17,
        full_name="C:/documents/first.hwp",
        page=1,
        start=1,
        count=1,
    )
    second = _table_inspection(
        document_id=23,
        full_name="C:/documents/second.hwp",
        page=1,
        start=2,
        count=1,
    )

    # When
    table_store.remember_inspection(first)
    object_store.remember_inspection(first)
    same_document_table = table_store.resolve(PublicTableTarget(target_id=table_opaque))
    same_document_object = object_store.resolve_picture(object_opaque, None)
    table_store.remember_inspection(second)
    object_store.remember_inspection(second)
    raw_after_transition = table_store.resolve(
        PublicTableTarget(
            target_id="table-1",
            document_path=second.full_name,
        )
    )

    # Then
    assert same_document_table.document_path == first.full_name
    assert same_document_object is not None
    assert same_document_object.document_path == first.full_name
    with pytest.raises(UnknownPublicTargetError):
        _ = table_store.resolve(PublicTableTarget(target_id=table_opaque))
    assert object_store.resolve_picture(object_opaque, None) is None
    assert raw_after_transition.document_path == second.full_name
    assert raw_after_transition.target.page_hint is None


class _PoisonedIdentityDocument:
    Modified: ClassVar[bool] = False

    @property
    def DocumentID(self) -> int:
        raise AssertionError("cached document identity must be used")

    @property
    def FullName(self) -> str:
        raise AssertionError("cached document identity must be used")

    @property
    def Format(self) -> str:
        raise AssertionError("cached document metadata must be used")

    @property
    def EditMode(self) -> int:
        raise AssertionError("cached document metadata must be used")


class _ActiveIdentityDocument:
    DocumentID: ClassVar[int] = 17
    FullName: ClassVar[str] = "C:/documents/sample.hwp"


class _CandidateApplication:
    PageCount: ClassVar[int] = 3

    class XHwpDocuments:
        Active_XHwpDocument: ClassVar[_ActiveIdentityDocument] = (
            _ActiveIdentityDocument()
        )


def test_candidate_uses_identity_captured_during_rot_scan() -> None:
    candidate = HwpDocumentCandidate(
        selector="active-document",
        moniker_name="!HwpObject.17.1",
        application=cast(
            HwpComApplication,
            cast(object, _CandidateApplication()),
        ),
        document=cast(
            HwpComDocument,
            cast(object, _PoisonedIdentityDocument()),
        ),
        document_id=17,
        full_name="C:/documents/sample.hwp",
        document_format="HWP",
        edit_mode=1,
        window_handle=101,
        active=True,
    )

    public = candidate.public()
    require_active_candidate(candidate, candidate.application)

    assert public.document_id == 17
    assert public.full_name == "C:/documents/sample.hwp"
    assert public.format == "HWP"
    assert public.edit_mode == 1


class _RevisionSignal:
    def __init__(self) -> None:
        self.revision: int = 0

    def start(self, process_id: int, moniker_name: str | None = None) -> None:
        _ = (process_id, moniker_name)

    def sequence(self) -> int:
        return self.revision

    def wait(self, after_sequence: int, timeout_seconds: float) -> int:
        _ = (after_sequence, timeout_seconds)
        return self.revision

    def stop(self) -> None:
        pass


class _InspectionController:
    def __init__(self) -> None:
        self.calls: int = 0

    def inspect_page_fast(
        self,
        session_id: str,
        page: int,
        *,
        include_cells: bool,
    ) -> FastPageInspection:
        _ = (session_id, include_cells)
        self.calls += 1
        return FastPageInspection(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            page=page,
            page_count=3,
            text="",
            controls=(),
        )

    def close(self) -> None:
        pass

    def restore_activation(self) -> None:
        pass

    def replace_selection(
        self,
        session_id: str,
        expected_selection: SelectionPosition,
        expected_text: str,
        replacement: str,
    ) -> None:
        _ = (session_id, expected_selection, expected_text, replacement)


def test_fast_inspection_reuses_only_unchanged_revision() -> None:
    controller = _InspectionController()
    signal = _RevisionSignal()
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        change_signal=signal,
    )
    events = cast(dict[int, _RevisionSignal], getattr(bridge, "_events"))
    session_processes = cast(dict[str, int], getattr(bridge, "_session_processes"))
    process_sessions = cast(dict[int, set[str]], getattr(bridge, "_process_sessions"))
    events[0] = signal
    session_processes["session"] = 0
    process_sessions[0] = {"session"}
    try:
        first = bridge.inspect_page_fast("session", 2, include_cells=False)
        second = bridge.inspect_page_fast("session", 2, include_cells=False)
        signal.revision += 1
        changed = bridge.inspect_page_fast("session", 2, include_cells=False)
        _ = bridge.replace_selection(
            "session",
            SelectionPosition(
                selected=False,
                start_list=0,
                start_paragraph=0,
                start_character=0,
                end_list=0,
                end_paragraph=0,
                end_character=0,
            ),
            "",
            "replacement",
        )
        after_mutation = bridge.inspect_page_fast("session", 2, include_cells=False)
    finally:
        bridge.close()

    assert first == second == changed == after_mutation
    assert controller.calls == 3
