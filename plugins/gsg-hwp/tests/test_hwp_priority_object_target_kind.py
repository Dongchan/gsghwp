"""target.kind must constrain object-recipe control resolution.

The resolver used to ignore ``target.kind`` entirely, so a caption request that
named a table could land on a picture, and an explicit ``control_instance_id``
was returned without checking that the object exists or that its type matches.
"""

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
from hwp_live_api import LiveHwpApplication  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    NativeCharacterFormat,
    NativePageControl,
    NativePageInspection,
    NativeParagraphFormat,
    NativePosition,
    NativeSelection,
    NativeSnapshot,
)
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402
from hwp_operation_contract import (  # noqa: E402
    HwpOperateTarget,
    OperationResult,
    WorkflowResolution,
)
from hwp_priority_object_inputs import (  # noqa: E402
    ControlTargetFailure,
    ControlTargetRequest,
    ResolvedObjectControl,
    resolve_control_target,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs  # noqa: E402


_DOCUMENT_ID = 17
_DOCUMENT_PATH = "C:/documents/sample.hwp"
_WINDOW_HANDLE = 91
_PAGE = 3
_ANCHOR = NativePosition(0, 3, 0)

# caption.add accepts both tables and pictures; image.replace accepts pictures only.
_CAPTION_TYPES = frozenset(("tbl", "gso", "pic", "picture"))
_PICTURE_TYPES = frozenset(("gso", "pic", "picture"))

# Phrases that would push the model to invent a control id instead of inspecting.
_FABRICATION_PROMPTS = (
    "지정하세요",
    "지정해",
    "입력하세요",
    "제공하세요",
    "알려주세요",
    "주세요",
)


def _control(control_type: str, instance_id: str) -> NativePageControl:
    return NativePageControl(control_type, instance_id, _ANCHOR, None, None)


def _page(*controls: NativePageControl) -> NativePageInspection:
    return NativePageInspection(
        document_id=_DOCUMENT_ID,
        full_name=_DOCUMENT_PATH,
        page=_PAGE,
        page_count=_PAGE,
        text="",
        controls=controls,
    )


def _selection_snapshot(control_type: str, control_instance_id: str) -> NativeSnapshot:
    cursor = _ANCHOR
    return NativeSnapshot(
        document_id=_DOCUMENT_ID,
        full_name=_DOCUMENT_PATH,
        current_page=_PAGE,
        page_count=_PAGE,
        modified=False,
        cursor=cursor,
        selection=NativeSelection(True, cursor, cursor),
        selected_text="",
        control_type=control_type,
        control_instance_id=control_instance_id,
        cell_address="",
        style_id=0,
        character_format=NativeCharacterFormat("함초롬바탕", 1_000, False, 0),
        paragraph_format=NativeParagraphFormat(0, 160, 0, 0, 0, 0, 0),
    )


def _resolve(
    page: NativePageInspection,
    target: HwpOperateTarget | None,
    *,
    control_types: frozenset[str] = _CAPTION_TYPES,
    snapshot: NativeSnapshot | None = None,
) -> ResolvedObjectControl | ControlTargetFailure | None:
    return resolve_control_target(
        ControlTargetRequest(page, target, control_types, snapshot)
    )


def _resolved(
    page: NativePageInspection,
    target: HwpOperateTarget | None,
    *,
    control_types: frozenset[str] = _CAPTION_TYPES,
    snapshot: NativeSnapshot | None = None,
) -> ResolvedObjectControl:
    result = _resolve(
        page,
        target,
        control_types=control_types,
        snapshot=snapshot,
    )
    assert isinstance(result, ResolvedObjectControl)
    return result


def _failure(
    page: NativePageInspection,
    target: HwpOperateTarget | None,
    *,
    control_types: frozenset[str] = _CAPTION_TYPES,
    snapshot: NativeSnapshot | None = None,
) -> ControlTargetFailure:
    result = _resolve(
        page,
        target,
        control_types=control_types,
        snapshot=snapshot,
    )
    assert isinstance(result, ControlTargetFailure)
    return result


# --------------------------------------------------------------------------
# target.kind constrains the candidate pool
# --------------------------------------------------------------------------


def test_table_kind_counts_only_tables_for_table_index() -> None:
    """The core defect: table_index used to index a table+picture mixed list."""
    page = _page(
        _control("gso", "picture-1"),
        _control("tbl", "table-1"),
        _control("tbl", "table-2"),
    )

    first = _resolved(page, HwpOperateTarget(kind="table", table_index=1))
    second = _resolved(page, HwpOperateTarget(kind="table", table_index=2))

    assert first.instance_id == "table-1"
    assert first.control_type == "tbl"
    assert second.instance_id == "table-2"


def test_table_kind_ignores_pictures_when_picking_the_unique_control() -> None:
    page = _page(_control("gso", "picture-1"), _control("tbl", "table-1"))

    resolved = _resolved(page, HwpOperateTarget(kind="table"))

    assert resolved.instance_id == "table-1"
    assert resolved.control_type == "tbl"


def test_picture_kind_ignores_tables_when_picking_the_unique_control() -> None:
    page = _page(_control("tbl", "table-1"), _control("gso", "picture-1"))

    resolved = _resolved(page, HwpOperateTarget(kind="picture"))

    assert resolved.instance_id == "picture-1"
    assert resolved.control_type == "gso"


def test_table_kind_does_not_fall_back_to_a_picture() -> None:
    """A table request on a picture-only page must not edit the picture."""
    page = _page(_control("gso", "picture-1"))

    assert _resolve(page, HwpOperateTarget(kind="table")) is None


def test_picture_kind_does_not_fall_back_to_a_table() -> None:
    page = _page(_control("tbl", "table-1"))

    assert _resolve(page, HwpOperateTarget(kind="picture")) is None


# --------------------------------------------------------------------------
# explicit control_instance_id is checked for existence and type
# --------------------------------------------------------------------------


def test_missing_explicit_control_instance_id_is_rejected() -> None:
    page = _page(_control("tbl", "table-1"))

    failure = _failure(
        page,
        HwpOperateTarget(kind="table", control_instance_id="table-9"),
    )

    assert failure.status == "not_found"
    assert "table-9" in failure.message


def test_explicit_control_instance_id_of_the_wrong_kind_is_rejected() -> None:
    page = _page(_control("tbl", "table-1"), _control("gso", "picture-1"))

    failure = _failure(
        page,
        HwpOperateTarget(kind="table", control_instance_id="picture-1"),
    )

    assert failure.status == "schema_conflict"
    assert "picture-1" in failure.message


def test_explicit_control_instance_id_of_the_right_kind_still_resolves() -> None:
    page = _page(_control("tbl", "table-1"), _control("gso", "picture-1"))

    resolved = _resolved(
        page,
        HwpOperateTarget(kind="table", control_instance_id="table-1"),
    )

    assert resolved.instance_id == "table-1"
    assert resolved.basis == "explicit_control_instance_id"
    assert resolved.control_type == "tbl"


def test_table_kind_conflicts_with_a_picture_only_workflow() -> None:
    """image.replace never edits a table, so kind=table is a contradiction."""
    page = _page(_control("gso", "picture-1"))

    failure = _failure(
        page,
        HwpOperateTarget(kind="table", control_instance_id="picture-1"),
        control_types=_PICTURE_TYPES,
    )

    assert failure.status == "schema_conflict"


def test_out_of_range_table_index_is_rejected_without_inventing_an_id() -> None:
    page = _page(_control("tbl", "table-1"), _control("gso", "picture-1"))

    failure = _failure(page, HwpOperateTarget(kind="table", table_index=2))

    assert failure.status == "not_found"
    assert failure.required_inputs == ()


# --------------------------------------------------------------------------
# absent or non-specific kinds keep the mixed fallback
# --------------------------------------------------------------------------


def test_absent_target_keeps_the_mixed_unique_fallback() -> None:
    assert _resolved(_page(_control("tbl", "table-1")), None).instance_id == "table-1"
    assert _resolved(_page(_control("gso", "picture-1")), None).instance_id == (
        "picture-1"
    )


def test_absent_target_still_declines_a_mixed_page() -> None:
    page = _page(_control("tbl", "table-1"), _control("gso", "picture-1"))

    assert _resolve(page, None) is None


def test_control_kind_keeps_the_mixed_candidate_list() -> None:
    page = _page(_control("gso", "picture-1"), _control("tbl", "table-1"))

    resolved = _resolved(page, HwpOperateTarget(kind="control", table_index=1))

    assert resolved.instance_id == "picture-1"


@pytest.mark.parametrize("kind", ("document", "page", "selection", "control"))
def test_non_specific_kinds_do_not_narrow_candidates(kind: str) -> None:
    page = _page(_control("gso", "picture-1"))

    resolved = _resolved(page, HwpOperateTarget(kind=kind))  # type: ignore[arg-type]

    assert resolved.instance_id == "picture-1"


def test_selection_binding_is_unchanged_for_a_selection_kind() -> None:
    page = _page()

    resolved = _resolved(
        page,
        HwpOperateTarget(kind="selection", binding="selection"),
        snapshot=_selection_snapshot("tbl", "table-1"),
    )

    assert resolved.instance_id == "table-1"
    assert resolved.basis == "native.selection"


def test_selection_binding_respects_a_specific_kind() -> None:
    page = _page()

    result = _resolve(
        page,
        HwpOperateTarget(kind="picture", binding="selection"),
        snapshot=_selection_snapshot("tbl", "table-1"),
    )

    assert result is None


# --------------------------------------------------------------------------
# rejection messages must not push the model to invent a value
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "control_types"),
    (
        (HwpOperateTarget(kind="table", control_instance_id="table-9"), _CAPTION_TYPES),
        (
            HwpOperateTarget(kind="table", control_instance_id="picture-1"),
            _CAPTION_TYPES,
        ),
        (
            HwpOperateTarget(kind="table", control_instance_id="picture-1"),
            _PICTURE_TYPES,
        ),
        (HwpOperateTarget(kind="table", table_index=7), _CAPTION_TYPES),
    ),
)
def test_rejection_messages_do_not_invite_a_fabricated_value(
    target: HwpOperateTarget,
    control_types: frozenset[str],
) -> None:
    page = _page(_control("tbl", "table-1"), _control("gso", "picture-1"))

    failure = _failure(page, target, control_types=control_types)

    assert failure.message
    for phrase in _FABRICATION_PROMPTS:
        assert phrase not in failure.message
    assert failure.required_inputs == ()


# --------------------------------------------------------------------------
# the recipe surfaces the rejection instead of raising a misleading error
# --------------------------------------------------------------------------


def _candidate() -> HwpDocumentCandidate:
    return cast(
        HwpDocumentCandidate,
        cast(
            object,
            SimpleNamespace(
                window_handle=_WINDOW_HANDLE,
                document=SimpleNamespace(
                    DocumentID=_DOCUMENT_ID,
                    FullName=_DOCUMENT_PATH,
                ),
            ),
        ),
    )


def _operate_caption(
    routing_page: NativePageInspection,
    target: HwpOperateTarget,
) -> OperationResult:
    result = object_recipes.operate_object_recipe(
        _candidate(),
        cast(LiveHwpApplication, object()),
        routing_page,
        WorkflowResolution(
            query="caption.add",
            status="resolved",
            lookup_microseconds=0,
            workflow_id="caption.add",
        ),
        target,
        None,
        HwpPriorityRecipeInputs(caption_text="검증 캡션"),
        resolve_only=False,
        allow_document_change=True,
    )
    assert result is not None
    return result


def test_caption_add_reports_a_missing_control_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_structure_read(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a rejected target must not reach a native inspection")

    monkeypatch.setattr(
        object_recipes,
        "inspect_native_structure",
        unexpected_structure_read,
    )

    result = _operate_caption(
        _page(_control("tbl", "table-1")),
        HwpOperateTarget(kind="table", control_instance_id="table-9"),
    )

    assert result.status == "not_found"
    assert result.required_inputs == ()
    assert "table-9" in result.message


def test_caption_add_reports_a_kind_mismatch_instead_of_editing_a_picture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_structure_read(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a rejected target must not reach a native inspection")

    monkeypatch.setattr(
        object_recipes,
        "inspect_native_structure",
        unexpected_structure_read,
    )

    result = _operate_caption(
        _page(_control("tbl", "table-1"), _control("gso", "picture-1")),
        HwpOperateTarget(kind="table", control_instance_id="picture-1"),
    )

    assert result.status == "schema_conflict"
    assert "picture-1" in result.message
