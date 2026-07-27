from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_priority_object_recipes as object_recipes  # noqa: E402
import hwp_priority_object_runtime as object_runtime  # noqa: E402
from hwp_live_api import LiveHwpApplication  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    MovePositionCommand,
    NativeActionRequest,
    NativeActionResult,
    NativeCharacterFormat,
    NativeDetailedCaption,
    NativeDetailedControl,
    NativeDetailedInspection,
    NativePageControl,
    NativePageInspection,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_operation_contract import (  # noqa: E402
    HwpOperateAssets,
    HwpOperateTarget,
    HwpWorkflowId,
    OperationResult,
    WorkflowResolution,
)
from hwp_priority_recipe_contract import (  # noqa: E402
    HwpPriorityRecipeInputs,
    RecipePosition,
)


_DOCUMENT_ID = 17
_DOCUMENT_PATH = "C:/documents/sample.hwp"
_WINDOW_HANDLE = 91
_CHARACTER_FORMAT = NativeCharacterFormat("함초롬바탕", 1_000, False, 0)
_PARAGRAPH_FORMAT = NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0)


def _candidate() -> HwpDocumentCandidate:
    return cast(
        HwpDocumentCandidate,
        cast(
            object,
            SimpleNamespace(
                window_handle=_WINDOW_HANDLE,
                # The session-confirmed identity the native request is built
                # from; HwpDocumentCandidate always carries both fields.
                document_id=_DOCUMENT_ID,
                full_name=_DOCUMENT_PATH,
                document=SimpleNamespace(
                    DocumentID=_DOCUMENT_ID,
                    FullName=_DOCUMENT_PATH,
                ),
            ),
        ),
    )


def _snapshot(
    cursor: NativePosition,
    *,
    style_id: int = 0,
    selection: NativeSelection | None = None,
    control_type: str = "",
    control_instance_id: str = "",
) -> NativeSnapshot:
    return NativeSnapshot(
        document_id=_DOCUMENT_ID,
        full_name=_DOCUMENT_PATH,
        current_page=3,
        page_count=3,
        modified=True,
        cursor=cursor,
        selection=(
            NativeSelection(False, cursor, cursor) if selection is None else selection
        ),
        selected_text="",
        control_type=control_type,
        control_instance_id=control_instance_id,
        cell_address="",
        style_id=style_id,
        character_format=_CHARACTER_FORMAT,
        paragraph_format=_PARAGRAPH_FORMAT,
    )


def _routing_page(
    controls: tuple[NativePageControl, ...] = (),
) -> NativePageInspection:
    return NativePageInspection(
        document_id=_DOCUMENT_ID,
        full_name=_DOCUMENT_PATH,
        page=3,
        page_count=3,
        text="",
        controls=controls,
    )


def _install_runtime_result(
    monkeypatch: pytest.MonkeyPatch,
    after: NativeSnapshot,
) -> list[NativeActionRequest]:
    requests: list[NativeActionRequest] = []

    def execute_native_actions(
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        assert window_handle == _WINDOW_HANDLE
        assert minimum_version == 9
        requests.append(request)
        return NativeActionResult(
            commands_executed=len(request.commands),
            actions_executed=len(request.commands),
            text_insertions=0,
            image_insertions=0,
            elapsed_microseconds=25,
            created_control_ids=(),
        )

    def read_native_snapshot(window_handle: int) -> NativeSnapshot:
        assert window_handle == _WINDOW_HANDLE
        return after

    monkeypatch.setattr(
        object_runtime,
        "execute_native_actions",
        execute_native_actions,
    )
    monkeypatch.setattr(
        object_runtime,
        "read_native_snapshot",
        read_native_snapshot,
    )
    return requests


def _operate(
    workflow: HwpWorkflowId,
    *,
    routing_page: NativePageInspection | None = None,
    target: HwpOperateTarget | None = None,
    assets: HwpOperateAssets | None = None,
    recipe_inputs: HwpPriorityRecipeInputs | None = None,
) -> OperationResult:
    result = object_recipes.operate_object_recipe(
        _candidate(),
        cast(LiveHwpApplication, object()),
        _routing_page() if routing_page is None else routing_page,
        WorkflowResolution(
            query=workflow,
            status="resolved",
            lookup_microseconds=0,
            workflow_id=workflow,
        ),
        target,
        assets,
        recipe_inputs,
        resolve_only=False,
        allow_document_change=True,
    )
    assert result is not None
    return result


def _selection_target() -> HwpOperateTarget:
    return HwpOperateTarget(kind="selection", binding="selection")


def test_style_apply_single_paragraph_selection_uses_post_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = NativePosition(0, 3, 2)
    before = _snapshot(
        NativePosition(0, 3, 8),
        style_id=2,
        selection=NativeSelection(
            selected=True,
            start=start,
            end=NativePosition(0, 3, 8),
            mode=1,
        ),
    )
    after = _snapshot(start, style_id=11)
    before_reads: list[int] = []

    def read_before(window_handle: int) -> NativeSnapshot:
        before_reads.append(window_handle)
        return before

    monkeypatch.setattr(object_recipes, "read_native_snapshot", read_before)
    requests = _install_runtime_result(monkeypatch, after)

    result = _operate(
        "style.apply",
        target=_selection_target(),
        recipe_inputs=HwpPriorityRecipeInputs(style_id=11),
    )

    assert result.verified is True
    assert before_reads == [_WINDOW_HANDLE]
    assert len(requests) == 1
    move = requests[0].commands[0]
    assert isinstance(move, MovePositionCommand)
    assert move.position == start


def test_style_apply_mismatched_style_is_not_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = NativePosition(0, 3, 2)
    before = _snapshot(
        start,
        style_id=2,
        selection=NativeSelection(
            selected=True,
            start=start,
            end=NativePosition(0, 3, 8),
            mode=1,
        ),
    )

    def read_before(_window_handle: int) -> NativeSnapshot:
        return before

    monkeypatch.setattr(object_recipes, "read_native_snapshot", read_before)
    _ = _install_runtime_result(monkeypatch, _snapshot(start, style_id=2))

    result = _operate(
        "style.apply",
        target=_selection_target(),
        recipe_inputs=HwpPriorityRecipeInputs(style_id=11),
    )

    assert result.verified is False


def test_style_apply_matching_style_at_wrong_position_is_not_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = NativePosition(0, 3, 2)
    before = _snapshot(
        start,
        style_id=2,
        selection=NativeSelection(
            selected=True,
            start=start,
            end=NativePosition(0, 3, 8),
            mode=1,
        ),
    )

    def read_before(_window_handle: int) -> NativeSnapshot:
        return before

    monkeypatch.setattr(object_recipes, "read_native_snapshot", read_before)
    _ = _install_runtime_result(
        monkeypatch,
        _snapshot(NativePosition(0, 4, 2), style_id=11),
    )

    result = _operate(
        "style.apply",
        target=_selection_target(),
        recipe_inputs=HwpPriorityRecipeInputs(style_id=11),
    )

    assert result.verified is False


@pytest.mark.parametrize(
    "selection",
    (
        NativeSelection(
            True,
            NativePosition(0, 3, 2),
            NativePosition(0, 4, 1),
            mode=1,
        ),
        NativeSelection(
            True,
            NativePosition(0, 3, 2),
            NativePosition(1, 3, 8),
            mode=1,
        ),
        NativeSelection(
            True,
            NativePosition(8, 0, 0),
            NativePosition(9, 0, 0),
            mode=3,
            cell_addresses=("A1", "A2"),
        ),
        NativeSelection(
            True,
            NativePosition(0, 3, 0),
            NativePosition(0, 3, 0),
            mode=2,
        ),
    ),
)
def test_style_apply_insufficient_selection_scope_is_not_verified(
    monkeypatch: pytest.MonkeyPatch,
    selection: NativeSelection,
) -> None:
    before = _snapshot(selection.start, style_id=2, selection=selection)

    def read_before(_window_handle: int) -> NativeSnapshot:
        return before

    monkeypatch.setattr(object_recipes, "read_native_snapshot", read_before)
    _ = _install_runtime_result(
        monkeypatch,
        _snapshot(selection.start, style_id=11),
    )

    result = _operate(
        "style.apply",
        target=_selection_target(),
        recipe_inputs=HwpPriorityRecipeInputs(style_id=11),
    )

    assert result.verified is None


def test_style_apply_explicit_position_is_verified_without_selection_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    position = NativePosition(0, 5, 0)

    def unexpected_selection_read(_window_handle: int) -> NativeSnapshot:
        raise AssertionError("an explicit style position must not read a selection")

    monkeypatch.setattr(
        object_recipes,
        "read_native_snapshot",
        unexpected_selection_read,
    )
    _ = _install_runtime_result(monkeypatch, _snapshot(position, style_id=11))

    result = _operate(
        "style.apply",
        recipe_inputs=HwpPriorityRecipeInputs(
            style_id=11,
            target_position=RecipePosition(
                list_id=position.list_id,
                paragraph=position.paragraph,
                character=position.character,
            ),
        ),
    )

    assert result.verified is True


def test_sizeless_image_replace_remains_unverified_without_content_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = NativePosition(0, 3, 0)
    picture = NativeDetailedControl(
        control_type="gso",
        instance_id="picture-7",
        user_description="그림",
        anchor=anchor,
        page_start=3,
        page_end=3,
        top_level=True,
        rows=None,
        columns=None,
        width_hwpunit=8_638,
        height_hwpunit=5_669,
    )
    inspection_calls: list[int] = []

    def inspect_native_structure(
        _window_handle: int,
        page: int,
    ) -> NativeDetailedInspection:
        inspection_calls.append(page)
        return NativeDetailedInspection(
            document_id=_DOCUMENT_ID,
            full_name=_DOCUMENT_PATH,
            page=3,
            page_count=3,
            text="",
            controls=(picture,),
            cells=(),
            captions=(),
        )

    monkeypatch.setattr(
        object_recipes,
        "inspect_native_structure",
        inspect_native_structure,
    )
    _ = _install_runtime_result(
        monkeypatch,
        _snapshot(
            anchor,
            control_type="gso",
            control_instance_id="picture-7",
        ),
    )
    routing_page = _routing_page(
        (
            NativePageControl(
                "gso",
                "picture-7",
                anchor,
                None,
                None,
                picture.width_hwpunit,
                picture.height_hwpunit,
            ),
        )
    )

    result = _operate(
        "image.replace",
        routing_page=routing_page,
        target=HwpOperateTarget(
            kind="picture",
            control_instance_id="picture-7",
        ),
        assets=HwpOperateAssets(images={"replacement": Path("replacement.png")}),
        recipe_inputs=HwpPriorityRecipeInputs(),
    )

    assert result.verified is None
    assert inspection_calls == [3]


def test_image_insert_existing_verification_remains_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = NativePosition(0, 3, 2)
    created = NativeDetailedControl(
        control_type="gso",
        instance_id="picture-created",
        user_description="그림",
        anchor=anchor,
        page_start=3,
        page_end=3,
        top_level=True,
        rows=None,
        columns=None,
        width_hwpunit=8_638,
        height_hwpunit=5_669,
    )
    inspections = iter(
        (
            NativeDetailedInspection(
                _DOCUMENT_ID,
                _DOCUMENT_PATH,
                3,
                3,
                "",
                (),
                (),
                (),
            ),
            NativeDetailedInspection(
                _DOCUMENT_ID,
                _DOCUMENT_PATH,
                3,
                3,
                "",
                (created,),
                (),
                (),
            ),
        )
    )

    def inspect_native_structure(
        _window_handle: int,
        _page: int,
    ) -> NativeDetailedInspection:
        return next(inspections)

    monkeypatch.setattr(
        object_recipes,
        "inspect_native_structure",
        inspect_native_structure,
    )
    _ = _install_runtime_result(monkeypatch, _snapshot(anchor))

    result = _operate(
        "image.insert",
        assets=HwpOperateAssets(images={"new": Path("new.png")}),
        recipe_inputs=HwpPriorityRecipeInputs(
            target_position=RecipePosition(
                list_id=anchor.list_id,
                paragraph=anchor.paragraph,
                character=anchor.character,
            )
        ),
    )

    assert result.verified is True
    assert result.created_control_ids == ("picture-created",)


def test_caption_add_existing_verification_remains_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = NativePosition(0, 3, 0)
    table = NativeDetailedControl(
        control_type="tbl",
        instance_id="table-7",
        user_description="표",
        anchor=anchor,
        page_start=3,
        page_end=3,
        top_level=True,
        rows=2,
        columns=2,
    )
    inspections = iter(
        (
            NativeDetailedInspection(
                _DOCUMENT_ID,
                _DOCUMENT_PATH,
                3,
                3,
                "",
                (table,),
                (),
                (),
            ),
            NativeDetailedInspection(
                _DOCUMENT_ID,
                _DOCUMENT_PATH,
                3,
                3,
                "검증 캡션",
                (table,),
                (),
                (
                    NativeDetailedCaption(
                        table_instance_id="table-7",
                        text="검증 캡션",
                        automatic_number=False,
                        style_id=4,
                        style_name="바탕글",
                        page_start=3,
                        page_end=3,
                    ),
                ),
            ),
        )
    )

    def inspect_native_structure(
        _window_handle: int,
        _page: int,
    ) -> NativeDetailedInspection:
        return next(inspections)

    monkeypatch.setattr(
        object_recipes,
        "inspect_native_structure",
        inspect_native_structure,
    )
    _ = _install_runtime_result(monkeypatch, _snapshot(anchor))
    routing_page = _routing_page(
        (
            NativePageControl(
                "tbl",
                "table-7",
                anchor,
                2,
                2,
            ),
        )
    )

    result = _operate(
        "caption.add",
        routing_page=routing_page,
        target=HwpOperateTarget(
            kind="table",
            control_instance_id="table-7",
        ),
        recipe_inputs=HwpPriorityRecipeInputs(caption_text="검증 캡션"),
    )

    assert result.verified is True
