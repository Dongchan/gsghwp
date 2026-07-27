from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast, final, override

import anyio
import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_priority_object_runtime as object_runtime  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    InsertTextCommand,
    NativeActionRequest,
    NativeDetailedControl,
    NativePageControl,
    NativePageInspection,
    NativePosition,
    RunCommand,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_structure_contract import (  # noqa: E402
    FastPageControl,
    FastPageInspection,
    StructurePosition,
)
from hwp_operation_contract import (  # noqa: E402
    HwpOperateGuards,
    HwpOperateInputs,
    OperationResult,
    WorkflowTargetCandidate,
)
from hwp_operation_descriptor import operation_descriptor  # noqa: E402
from hwp_public_action_contract import (  # noqa: E402
    PublicActionExecutor,
    PublicObjectTargetStore,
)
from hwp_public_object_tools import HwpPublicObjectTools  # noqa: E402
from hwp_public_contract import PublicActionResult, to_public_action_result  # noqa: E402


def _picture(
    instance_id: str,
    *,
    page: int = 3,
    width_hwpunit: int = 8_504,
    height_hwpunit: int = 5_669,
) -> NativeDetailedControl:
    return NativeDetailedControl(
        "gso",
        instance_id,
        "그림",
        NativePosition(0, 0, 0),
        page,
        page,
        True,
        None,
        None,
        width_hwpunit,
        height_hwpunit,
    )


def _expectation(
    *,
    pages: frozenset[int] | None = None,
) -> object_runtime.PictureInsertExpectation:
    return object_runtime.PictureInsertExpectation(
        fitted_width_mm=30,
        fitted_height_mm=20,
        document_end_pages=frozenset((2, 3)) if pages is None else pages,
    )


def test_image_insert_readback_verifies_one_sized_picture_and_reports_size() -> None:
    # Given
    before = (_picture("existing", page=2),)
    after = (*before, _picture("created"))

    # When
    created = object_runtime.verify_inserted_picture(before, after, _expectation())

    # Then
    assert created.instance_id == "created"
    assert abs(created.width_mm - 30.0) <= 0.001
    assert abs(created.height_mm - 20.0) <= 0.002


def test_image_insert_readback_accepts_source_ratio_fit_inside_requested_box() -> None:
    # Given
    before = (_picture("existing", page=2),)
    fitted_width = 14.142857
    fitted_height = 20.0
    created = _picture(
        "created",
        width_hwpunit=round(fitted_width * 7_200 / 25.4),
        height_hwpunit=round(fitted_height * 7_200 / 25.4),
    )
    expectation = object_runtime.PictureInsertExpectation(
        fitted_width_mm=fitted_width,
        fitted_height_mm=fitted_height,
        document_end_pages=frozenset((2, 3)),
    )

    # When
    verified = object_runtime.verify_inserted_picture(
        before,
        (*before, created),
        expectation,
    )

    # Then
    assert verified.instance_id == "created"
    assert abs(verified.width_mm - fitted_width) <= 0.005
    assert abs(verified.height_mm - fitted_height) <= 0.005


def test_image_replace_readback_verifies_same_picture_id_and_fitted_size() -> None:
    before = _picture("picture-7", width_hwpunit=8_504, height_hwpunit=5_669)
    expected_width = 14.142857
    expected_height = 20.0
    after = _picture(
        "picture-7",
        width_hwpunit=round(expected_width * 7_200 / 25.4),
        height_hwpunit=round(expected_height * 7_200 / 25.4),
    )

    verified = object_runtime.verify_replaced_picture(
        before,
        after,
        object_runtime.PictureReplaceExpectation(
            control_id="picture-7",
            fitted_width_mm=expected_width,
            fitted_height_mm=expected_height,
        ),
    )

    assert verified.instance_id == "picture-7"
    assert abs(verified.width_mm - expected_width) <= 0.005
    assert abs(verified.height_mm - expected_height) <= 0.005


@pytest.mark.parametrize(
    "after",
    (
        _picture("different-picture"),
        _picture("picture-7", width_hwpunit=7_000),
        _picture("picture-7", height_hwpunit=4_000),
    ),
)
def test_image_replace_readback_rejects_wrong_target_or_size(
    after: NativeDetailedControl,
) -> None:
    before = _picture("picture-7")
    expectation = object_runtime.PictureReplaceExpectation(
        control_id="picture-7",
        fitted_width_mm=30.0,
        fitted_height_mm=20.0,
    )

    with pytest.raises(HwpLiveError):
        _ = object_runtime.verify_replaced_picture(before, after, expectation)


def test_image_insert_readback_rejects_arbitrary_size_inside_requested_box() -> None:
    # Given
    before = (_picture("existing", page=2),)
    arbitrary = _picture(
        "created",
        width_hwpunit=round(10.0 * 7_200 / 25.4),
        height_hwpunit=round(10.0 * 7_200 / 25.4),
    )
    expectation = object_runtime.PictureInsertExpectation(
        fitted_width_mm=14.142857,
        fitted_height_mm=20.0,
        document_end_pages=frozenset((2, 3)),
    )

    # When / Then
    with pytest.raises(HwpLiveError):
        _ = object_runtime.verify_inserted_picture(
            before,
            (*before, arbitrary),
            expectation,
        )


@pytest.mark.parametrize(
    ("after", "pages"),
    (
        ((_picture("existing", page=2),), frozenset((2, 3))),
        (
            (
                _picture("existing", page=2),
                _picture("created-1"),
                _picture("created-2"),
            ),
            frozenset((2, 3)),
        ),
        (
            (
                _picture("existing", page=2),
                _picture("created", width_hwpunit=7_000),
            ),
            frozenset((2, 3)),
        ),
        (
            (
                _picture("existing", page=2),
                _picture("created", page=1),
            ),
            frozenset((2, 3)),
        ),
    ),
)
def test_image_insert_readback_rejects_unverified_transitions(
    after: tuple[NativeDetailedControl, ...],
    pages: frozenset[int],
) -> None:
    # Given
    before = (_picture("existing", page=2),)
    expectation = _expectation(pages=pages)

    # When / Then
    with pytest.raises(HwpLiveError):
        _ = object_runtime.verify_inserted_picture(before, after, expectation)


@pytest.mark.parametrize(
    ("width_mm", "height_mm", "created"),
    (
        (30.0, None, _picture("created", width_hwpunit=7_000)),
        (None, 20.0, _picture("created", height_hwpunit=4_000)),
    ),
)
def test_image_insert_readback_rejects_each_fitted_dimension_independently(
    width_mm: float | None,
    height_mm: float | None,
    created: NativeDetailedControl,
) -> None:
    # Given
    before = (_picture("existing", page=2),)
    expectation = object_runtime.PictureInsertExpectation(
        fitted_width_mm=width_mm,
        fitted_height_mm=height_mm,
        document_end_pages=frozenset((2, 3)),
    )

    # When / Then
    with pytest.raises(HwpLiveError):
        _ = object_runtime.verify_inserted_picture(
            before,
            (*before, created),
            expectation,
        )


def _page_control(
    control_type: str,
    instance_id: str,
    anchor: NativePosition,
) -> NativePageControl:
    return NativePageControl(
        control_type=control_type,
        instance_id=instance_id,
        anchor=anchor,
        rows=None,
        columns=None,
    )


def _page(
    text: str,
    controls: tuple[NativePageControl, ...],
) -> NativePageInspection:
    return NativePageInspection(
        document_id=17,
        full_name="C:/documents/sample.hwp",
        page=3,
        page_count=3,
        text=text,
        controls=controls,
    )


def test_picture_caption_readback_verifies_text_auto_number_and_anchor() -> None:
    # Given
    picture_anchor = NativePosition(0, 9, 0)
    expectation = object_runtime.PictureCaptionExpectation(
        control_id="picture-7",
        caption_text="Q 그림 캡션",
        anchor=picture_anchor,
        existing_auto_number_count=1,
    )
    after = _page(
        "그림 7 Q 그림 캡션\r\n",
        (
            _page_control("gso", "picture-7", picture_anchor),
            _page_control("atno", "", NativePosition(4, 0, 0)),
            _page_control("atno", "", NativePosition(8, 0, 0)),
        ),
    )

    # When
    verified_id = object_runtime.verify_picture_caption(after, expectation)

    # Then
    assert verified_id == "picture-7"


@pytest.mark.parametrize(
    ("selected_text", "prefix", "literal"),
    (
        (
            "(그림 5.5.3-16)T4 양식 상속 이미지 캡션\r\n",
            "(그림 5.5.3-16)",
            "T4 양식 상속 이미지 캡션",
        ),
        ("그림 7 Q 그림 캡션", "그림 7 ", "Q 그림 캡션"),
        ("[그림 3-2] 제목", "[그림 3-2]", " 제목"),
    ),
)
def test_picture_caption_selection_splits_auto_number_from_literal(
    selected_text: str,
    prefix: str,
    literal: str,
) -> None:
    # Given / When
    parsed = object_runtime.parse_picture_caption_selection(selected_text)

    # Then
    assert parsed is not None
    assert parsed.automatic_prefix == prefix
    assert parsed.literal_text == literal


def test_picture_caption_probe_reads_exact_selected_caption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    def execute(
        window_handle: int,
        request: object,
        *,
        minimum_version: int,
    ) -> object:
        captured.extend((window_handle, request, minimum_version))
        return SimpleNamespace(commands_executed=5, elapsed_microseconds=120)

    monkeypatch.setattr(object_runtime, "execute_native_actions", execute)

    def read_snapshot(_window_handle: int) -> object:
        return SimpleNamespace(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            selected_text="(그림 5.5.3-16)기존 캡션\r\n",
        )

    monkeypatch.setattr(object_runtime, "read_native_snapshot", read_snapshot)
    candidate = cast(
        HwpDocumentCandidate,
        cast(
            object,
            SimpleNamespace(
                window_handle=91,
                # The session-confirmed identity the native request is built
                # from; HwpDocumentCandidate always carries both fields.
                document_id=17,
                full_name="C:/documents/sample.hwp",
                document=SimpleNamespace(
                    DocumentID=17,
                    FullName="C:/documents/sample.hwp",
                ),
            ),
        ),
    )

    probed = object_runtime.probe_picture_caption(candidate, "picture-7")

    assert probed.caption is not None
    assert probed.caption.automatic_prefix == "(그림 5.5.3-16)"
    assert probed.caption.literal_text == "기존 캡션"
    assert probed.commands_executed == 5
    assert probed.elapsed_microseconds == 120
    assert captured[0] == 91
    request = cast(NativeActionRequest, captured[1])
    assert [type(command).__name__ for command in request.commands] == [
        "SelectControlCommand",
        "CaptureTableCommand",
        "RunCommand",
        "RunCommand",
        "RunCommand",
    ]
    assert captured[2] == 9


def test_existing_picture_caption_commands_replace_only_literal_suffix() -> None:
    caption = object_runtime.PictureCaptionText(
        automatic_prefix="(그림 5.5.3-16)",
        literal_text="기존 캡션",
    )

    commands = object_runtime.picture_caption_commands(
        "picture-7",
        caption,
        style_id=11,
        title="수정 캡션",
    )

    actions = tuple(
        command.action for command in commands if isinstance(command, RunCommand)
    )
    inserted = tuple(
        command.text for command in commands if isinstance(command, InsertTextCommand)
    )
    assert actions[:2] == ("ShapeObjAttachCaption", "MoveParaEnd")
    assert actions.count("MoveSelLeft") == len(caption.literal_text)
    assert "Delete" in actions
    assert "ShapeObjInsertCaptionNum" not in actions
    assert actions[-1] == "CloseEx"
    assert inserted == ("수정 캡션",)


def test_new_picture_caption_commands_reenter_probed_caption_context() -> None:
    commands = object_runtime.picture_caption_commands(
        "picture-7",
        None,
        style_id=11,
        title="새 캡션",
    )

    actions = tuple(
        command.action for command in commands if isinstance(command, RunCommand)
    )
    assert [type(command).__name__ for command in commands] == [
        "SelectControlCommand",
        "CaptureTableCommand",
        "RunCommand",
        "RunCommand",
        "InsertTextCommand",
        "RunCommand",
        "ParameterActionCommand",
        "RunCommand",
    ]
    assert actions == (
        "ShapeObjAttachCaption",
        "MoveParaEnd",
        "SelectAll",
        "CloseEx",
    )


def test_picture_caption_readback_update_preserves_auto_number_and_count() -> None:
    # Given
    picture_anchor = NativePosition(0, 9, 0)
    expectation = object_runtime.PictureCaptionExpectation(
        control_id="picture-7",
        caption_text="수정된 캡션",
        anchor=picture_anchor,
        existing_auto_number_count=2,
        automatic_prefix="(그림 5.5.3-16)",
    )
    after = _page(
        "(그림 5.5.3-16)수정된 캡션\r\n",
        (
            _page_control("gso", "picture-7", picture_anchor),
            _page_control("atno", "", NativePosition(4, 0, 0)),
            _page_control("atno", "", NativePosition(8, 0, 0)),
        ),
    )

    # When
    verified_id = object_runtime.verify_picture_caption(after, expectation)

    # Then
    assert verified_id == "picture-7"


@pytest.mark.parametrize(
    ("text", "automatic_number_count"),
    (
        ("(그림 5.5.3-17)수정된 캡션\r\n", 2),
        ("(그림 5.5.3-16)수정된 캡션\r\n", 3),
    ),
)
def test_picture_caption_readback_update_rejects_changed_auto_number(
    text: str,
    automatic_number_count: int,
) -> None:
    # Given
    picture_anchor = NativePosition(0, 9, 0)
    controls = [
        _page_control("gso", "picture-7", picture_anchor),
        *(
            _page_control("atno", "", NativePosition(index + 1, 0, 0))
            for index in range(automatic_number_count)
        ),
    ]
    expectation = object_runtime.PictureCaptionExpectation(
        control_id="picture-7",
        caption_text="수정된 캡션",
        anchor=picture_anchor,
        existing_auto_number_count=2,
        automatic_prefix="(그림 5.5.3-16)",
    )

    # When / Then
    with pytest.raises(HwpLiveError):
        _ = object_runtime.verify_picture_caption(
            _page(text, tuple(controls)),
            expectation,
        )


@pytest.mark.parametrize(
    "after",
    (
        _page(
            "다른 캡션\r\n",
            (
                _page_control("gso", "picture-7", NativePosition(0, 9, 0)),
                _page_control("atno", "atno-created", NativePosition(8, 0, 0)),
            ),
        ),
        _page(
            "그림 7 Q 그림 캡션\r\n",
            (_page_control("gso", "picture-7", NativePosition(0, 9, 0)),),
        ),
        _page(
            "그림 7 Q 그림 캡션\r\n",
            (
                _page_control("gso", "picture-7", NativePosition(0, 10, 0)),
                _page_control("atno", "atno-created", NativePosition(8, 0, 0)),
            ),
        ),
        _page(
            "그림 7 Q 그림 캡션\r\n",
            (
                _page_control("gso", "picture-7", NativePosition(0, 9, 0)),
                _page_control("atno", "", NativePosition(8, 0, 0)),
            ),
        ),
    ),
)
def test_picture_caption_readback_rejects_missing_evidence(
    after: NativePageInspection,
) -> None:
    # Given
    expectation = object_runtime.PictureCaptionExpectation(
        control_id="picture-7",
        caption_text="Q 그림 캡션",
        anchor=NativePosition(0, 9, 0),
        existing_auto_number_count=1,
    )

    # When / Then
    with pytest.raises(HwpLiveError):
        _ = object_runtime.verify_picture_caption(after, expectation)


def test_image_insert_descriptor_accepts_detailed_structure_readback() -> None:
    # Given / When
    descriptor = operation_descriptor("image.insert")

    # Then
    assert descriptor is not None
    assert "native_detailed_structure_before_after" in descriptor.verification_modes


def test_created_picture_id_is_exposed_as_a_public_target_id() -> None:
    # Given
    result = OperationResult(
        status="executed",
        changed=True,
        query="insert image",
        registry_entries=1,
        lookup_microseconds=0,
        message="ok",
        verified=True,
        created_control_ids=("native-picture-7",),
    )

    # When
    public_result = to_public_action_result(result, ())

    # Then
    assert public_result.verified is True
    assert public_result.created_target_ids == ("native-picture-7",)


def test_raw_object_ids_resolve_without_inspection_cache() -> None:
    # Given
    store = PublicObjectTargetStore()

    # When
    control = store.resolve_control("native-table-42", "C:/documents/sample.hwp")
    picture = store.resolve_picture("native-picture-7", None)
    store.clear()
    control_after_clear = store.resolve_control("native-table-42", None)

    # Then
    assert control is not None
    assert control.target.control_instance_id == "native-table-42"
    assert picture is not None
    assert picture.target.control_instance_id == "native-picture-7"
    assert control_after_clear is not None
    assert control_after_clear.target.control_instance_id == "native-table-42"


def test_opaque_object_ids_remain_cache_scoped() -> None:
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
        "C:/documents/sample.hwp",
    )[0]

    # When
    same_document = store.resolve_picture(
        opaque_id,
        "C:/documents/sample.hwp",
    )
    different_document = store.resolve_picture(
        opaque_id,
        "C:/documents/other.hwp",
    )
    store.clear()

    # Then
    assert same_document is not None
    assert different_document is None
    assert store.resolve_picture(opaque_id, None) is None


@final
class _CandidateExecutor(PublicActionExecutor):
    def __init__(self, inspection: FastPageInspection) -> None:
        self.inspection: FastPageInspection = inspection
        self.inspection_calls: int = 0

    @override
    async def execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationResult:
        _ = (intent, inputs, guards)
        raise AssertionError("unknown opaque target lookup must not mutate")

    @override
    async def inspect_page_fast(
        self,
        document_selector: str | None,
        page: int,
        include_cells: bool,
    ) -> FastPageInspection:
        _ = (document_selector, page, include_cells)
        self.inspection_calls += 1
        return self.inspection


async def _caption_with_missing_opaque_target(
    tools: HwpPublicObjectTools,
) -> PublicActionResult:
    return await tools.hwp_add_caption(
        operation_id="object-candidate-search",
        text="후보 검색",
        target_id="hwp-target-missing",
        document_path="C:/documents/sample.hwp",
    )


def test_unknown_opaque_target_returns_real_candidates() -> None:
    # Given
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
                anchor=StructurePosition(list_id=0, paragraph=1, character=0),
                rows=2,
                columns=3,
            ),
            FastPageControl(
                control_type="gso",
                instance_id="native-picture-7",
                anchor=StructurePosition(list_id=0, paragraph=2, character=0),
            ),
        ),
    )
    executor = _CandidateExecutor(inspection)
    tools = HwpPublicObjectTools(executor)

    # When
    result = anyio.run(_caption_with_missing_opaque_target, tools)

    # Then
    assert result.status == "needs_target"
    assert tuple(candidate.target_id for candidate in result.target_candidates) == (
        "native-table-42",
        "native-picture-7",
    )
    assert executor.inspection_calls == 1
