from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar, cast


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
from hwp_public_action_contract import PublicObjectTargetStore  # noqa: E402


def test_fast_inspection_instance_id_is_a_public_object_target() -> None:
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

    store.remember_inspection(inspection)
    resolved = store.resolve_control("native-table-42", None)

    assert resolved is not None
    assert resolved.document_path == inspection.full_name
    assert resolved.target.page_hint == inspection.page
    assert resolved.target.control_instance_id == "native-table-42"

    store.clear()

    assert store.resolve_control("native-table-42", None) is None


def test_new_fast_inspection_invalidates_previous_object_ids() -> None:
    store = PublicObjectTargetStore()
    first = FastPageInspection(
        document_id=17,
        full_name="C:/documents/first.hwp",
        page=1,
        page_count=2,
        text="",
        controls=(
            FastPageControl(
                control_type="tbl",
                instance_id="first-table",
                anchor=StructurePosition(list_id=0, paragraph=0, character=0),
                rows=1,
                columns=1,
            ),
        ),
    )
    second = first.model_copy(
        update={
            "document_id": 18,
            "full_name": "C:/documents/second.hwp",
            "controls": (
                FastPageControl(
                    control_type="gso",
                    instance_id="second-picture",
                    anchor=StructurePosition(
                        list_id=0,
                        paragraph=1,
                        character=0,
                    ),
                ),
            ),
        }
    )

    store.remember_inspection(first)
    store.remember_inspection(second)

    assert store.resolve_control("first-table", None) is None
    assert store.resolve_picture("second-picture", None) is not None


def test_fast_inspection_object_ids_are_bounded() -> None:
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

    store.remember_inspection(inspection)

    assert store.resolve_control("table-255", None) is not None
    assert store.resolve_control("table-256", None) is None


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
