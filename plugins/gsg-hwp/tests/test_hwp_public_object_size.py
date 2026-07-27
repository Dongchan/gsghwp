from __future__ import annotations

import sys
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import cast, final

import anyio
import pytest
from pydantic import ValidationError


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_priority_object_recipes as object_recipes  # noqa: E402
from hwp_live_api import LiveHwpApplication  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    BooleanValue,
    IntegerValue,
    MillimeterValue,
    NativeActionCommand,
    NativeCharacterFormat,
    NativeDetailedControl,
    NativeDetailedInspection,
    NativePageControl,
    NativePageInspection,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSetter,
    NativeSnapshot,
    ParameterActionCommand,
    SelectControlCommand,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_live_structure_contract import FastPageInspection  # noqa: E402
from hwp_operation_contract import (  # noqa: E402
    HwpOperateAssets,
    HwpOperateGuards,
    HwpOperateInputs,
    HwpOperateTarget,
    OperationResult,
    WorkflowResolution,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs  # noqa: E402
from hwp_public_action_contract import PublicImageSize  # noqa: E402
from hwp_public_object_tools import HwpPublicObjectTools  # noqa: E402


@final
class _CapturingExecutor:
    def __init__(self) -> None:
        self.inputs: HwpOperateInputs | None = None

    async def execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationResult:
        _ = intent, guards
        self.inputs = inputs
        return OperationResult(
            status="executed",
            changed=True,
            query="replace image",
            registry_entries=1,
            lookup_microseconds=0,
            message="ok",
            verified=True,
            commands_executed=1,
            modified=True,
            retry_safe=True,
        )

    async def inspect_page_fast(
        self,
        document_selector: str | None,
        page: int,
        include_cells: bool,
    ) -> FastPageInspection:
        _ = document_selector, page, include_cells
        raise AssertionError("a concrete native picture ID must not trigger inspection")


def test_replace_image_forwards_optional_fit_box_to_existing_recipe() -> None:
    executor = _CapturingExecutor()
    tools = HwpPublicObjectTools(executor)

    _ = anyio.run(
        partial(
            tools.hwp_replace_image,
            operation_id="replace-sized-picture",
            path=Path("replacement.png"),
            target_id="native-picture-7",
            width_mm=40.0,
            height_mm=20.0,
        )
    )

    assert executor.inputs is not None
    assert executor.inputs.recipe is not None
    assert executor.inputs.recipe.picture_width_mm == 40.0
    assert executor.inputs.recipe.picture_height_mm == 20.0


def test_replace_image_legacy_call_keeps_size_unspecified() -> None:
    executor = _CapturingExecutor()
    tools = HwpPublicObjectTools(executor)

    _ = anyio.run(
        partial(
            tools.hwp_replace_image,
            operation_id="replace-legacy-picture",
            path=Path("replacement.png"),
            target_id="native-picture-7",
        )
    )

    assert executor.inputs is not None
    assert executor.inputs.recipe is not None
    assert executor.inputs.recipe.picture_width_mm is None
    assert executor.inputs.recipe.picture_height_mm is None


def test_replace_image_rejects_one_sided_fit_box() -> None:
    executor = _CapturingExecutor()
    tools = HwpPublicObjectTools(executor)

    with pytest.raises(ValidationError, match="width_mm.*height_mm"):
        _ = anyio.run(
            partial(
                tools.hwp_replace_image,
                operation_id="replace-incomplete-size",
                path=Path("replacement.png"),
                target_id="native-picture-7",
                width_mm=40.0,
            )
        )


def test_public_and_recipe_picture_boxes_match_native_1000mm_limit() -> None:
    assert PublicImageSize(width_mm=1_000, height_mm=1_000).width_mm == 1_000
    recipe = HwpPriorityRecipeInputs(
        picture_width_mm=1_000,
        picture_height_mm=1_000,
    )
    assert recipe.picture_height_mm == 1_000
    with pytest.raises(ValidationError):
        _ = PublicImageSize(width_mm=1_000.1, height_mm=1_000)
    with pytest.raises(ValidationError):
        _ = HwpPriorityRecipeInputs(
            picture_width_mm=1_000,
            picture_height_mm=1_000.1,
        )


def test_sized_replace_resizes_picture_before_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = NativeDetailedControl(
        "gso",
        "picture-7",
        "그림",
        NativePosition(0, 3, 0),
        3,
        3,
        True,
        None,
        None,
        8_638,
        5_669,
    )
    after = NativeDetailedControl(
        "gso",
        "picture-7",
        "그림",
        before.anchor,
        3,
        3,
        True,
        None,
        None,
        round(30.0 * 7_200 / 25.4),
        round(20.0 * 7_200 / 25.4),
    )
    structures = iter(
        (
            NativeDetailedInspection(
                17,
                "C:/documents/sample.hwp",
                3,
                3,
                "",
                (before,),
                (),
                (),
            ),
            NativeDetailedInspection(
                17,
                "C:/documents/sample.hwp",
                3,
                3,
                "",
                (after,),
                (),
                (),
            ),
        )
    )

    def inspect(
        _window_handle: int,
        _page: int,
    ) -> NativeDetailedInspection | None:
        return next(structures)

    def fit(
        _path: Path,
        *,
        width_mm: float,
        height_mm: float,
    ) -> tuple[float, float]:
        return width_mm, height_mm

    monkeypatch.setattr(object_recipes, "inspect_native_structure", inspect)
    monkeypatch.setattr(object_recipes, "fit_image_in_box", fit)
    captured: list[NativeActionCommand] = []
    after_snapshot = NativeSnapshot(
        document_id=17,
        full_name="C:/documents/sample.hwp",
        current_page=3,
        page_count=3,
        modified=True,
        cursor=before.anchor,
        selection=NativeSelection(False, before.anchor, before.anchor),
        selected_text="",
        control_type="gso",
        control_instance_id="picture-7",
        cell_address="",
        style_id=0,
        character_format=NativeCharacterFormat("", 0, False, 0),
        paragraph_format=NativeParagraphFormat(0, 0, 0, 0, 0, 0, 0),
    )

    def execute(
        _candidate: HwpDocumentCandidate,
        commands: tuple[NativeActionCommand, ...],
    ) -> tuple[int, int, int, int, bool, NativeSnapshot]:
        captured.extend(commands)
        return len(commands), 100, 3, 3, True, after_snapshot

    monkeypatch.setattr(object_recipes, "_execute", execute)

    result = object_recipes.operate_object_recipe(
        cast(
            HwpDocumentCandidate,
            cast(object, SimpleNamespace(window_handle=91)),
        ),
        cast(LiveHwpApplication, cast(object, SimpleNamespace())),
        NativePageInspection(
            17,
            "C:/documents/sample.hwp",
            3,
            3,
            "",
            (
                NativePageControl(
                    "gso",
                    "picture-7",
                    before.anchor,
                    None,
                    None,
                    before.width_hwpunit,
                    before.height_hwpunit,
                ),
            ),
        ),
        WorkflowResolution(
            query="replace image",
            status="resolved",
            lookup_microseconds=0,
            workflow_id="image.replace",
        ),
        HwpOperateTarget(
            kind="picture",
            control_instance_id="picture-7",
        ),
        HwpOperateAssets(images={"replacement": Path("replacement.png")}),
        HwpPriorityRecipeInputs(
            picture_width_mm=30.0,
            picture_height_mm=20.0,
        ),
        resolve_only=False,
        allow_document_change=True,
    )

    assert result is not None
    assert result.verified is True

    def is_picture_change(command: NativeActionCommand) -> bool:
        return (
            isinstance(command, ParameterActionCommand)
            and command.action == "PictureChange"
        )

    change_index = next(
        index for index, command in enumerate(captured) if is_picture_change(command)
    )
    assert change_index == 4
    assert captured[change_index - 4] == SelectControlCommand("picture-7")
    assert captured[change_index - 3] == ParameterActionCommand(
        "ShapeObjDialog",
        "HShapeObject",
        (NativeSetter("HSet/ProtectSize", BooleanValue(False)),),
    )
    assert captured[change_index - 2] == ParameterActionCommand(
        "ShapeObjDialog",
        "HShapeObject",
        (
            NativeSetter("HSet/WidthRelTo", IntegerValue(4)),
            NativeSetter("HSet/Width", MillimeterValue(30.0)),
            NativeSetter("HSet/HeightRelTo", IntegerValue(2)),
            NativeSetter("HSet/Height", MillimeterValue(20.0)),
        ),
    )
    assert captured[change_index - 1] == SelectControlCommand("picture-7")
