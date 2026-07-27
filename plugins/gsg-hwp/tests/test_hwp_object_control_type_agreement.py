"""Every object path must judge "is this a picture / a table" by one rule.

The judgment used to be copied into fifteen places across five modules, and the
copies did not agree: ``hwp_live_preview`` casefolded ``control_type`` before
the membership test while every other path compared the raw string.  A single
uppercase ``CtrlID`` from Hancom would therefore make the preview count a
picture that the edit path could not find, and ``target.kind="picture"`` would
answer "이 작업은 그림 종류의 개체를 편집하지 않습니다" about a document that
plainly contains a picture.

These tests pin the three properties that matter:

* every path returns the *same* verdict for the same ``control_type``;
* table and picture never bleed into each other under any casing;
* the preview's own verdict is unchanged for every value ever observed.
"""

from __future__ import annotations

import asyncio
import re
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

import hwp_live_preview  # noqa: E402
import hwp_object_control_types as shared  # noqa: E402
import hwp_priority_object_inputs  # noqa: E402
import hwp_priority_object_recipes  # noqa: E402
import hwp_priority_object_runtime  # noqa: E402
import hwp_public_object_tools  # noqa: E402
from hwp_live_native_action_results import (  # noqa: E402
    NativeDetailedControl,
    NativeDetailedInspection,
    NativePageControl,
    NativePageInspection,
    NativePosition,
)
from hwp_operation_contract import HwpOperateTarget  # noqa: E402
from hwp_public_object_tools import HwpPublicObjectTools  # noqa: E402


# The five modules that own an object-kind judgment.  ``hwp_object_control_types``
# is deliberately absent: it is the one place the tokens may live.
OWNED_MODULES = (
    "hwp_priority_object_inputs.py",
    "hwp_priority_object_recipes.py",
    "hwp_priority_object_runtime.py",
    "hwp_public_object_tools.py",
    "hwp_live_preview.py",
)

# Control-type tokens.  These are Hancom ``CtrlID`` values, never public schema
# words -- ``target.kind`` uses "table"/"picture", which are different strings
# from the ``CtrlID`` tokens "tbl"/"gso"/"pic".
CONTROL_TYPE_TOKENS = ("gso", "tbl", "pic")

# Every casing an uppercase-emitting Hancom build could plausibly produce, plus
# the exact lowercase values captured from the real native bridge.
PICTURE_VALUES = (
    "gso",
    "GSO",
    "Gso",
    "pic",
    "PIC",
    "Pic",
    "picture",
    "PICTURE",
    "Picture",
)
TABLE_VALUES = ("tbl", "TBL", "Tbl")
# Observed in artifacts/live-validation captures: gso, tbl, cold, secd, pgnp, nwno.
NEITHER_VALUES = ("cold", "secd", "pgnp", "nwno", "atno", "COLD", "$pic", "")


def _detailed(control_type: str) -> NativeDetailedInspection:
    return NativeDetailedInspection(
        document_id=1,
        full_name="C:\\doc.hwp",
        page=1,
        page_count=1,
        text="",
        controls=(
            NativeDetailedControl(
                control_type=control_type,
                instance_id="obj-1",
                user_description="",
                anchor=NativePosition(0, 0, 0),
                page_start=1,
                page_end=1,
                top_level=True,
                rows=None,
                columns=None,
                width_hwpunit=1000,
                height_hwpunit=1000,
            ),
        ),
        cells=(),
        captions=(),
    )


def _page(control_type: str) -> NativePageInspection:
    return NativePageInspection(
        document_id=1,
        full_name="C:\\doc.hwp",
        page=1,
        page_count=1,
        text="",
        controls=(
            NativePageControl(
                control_type=control_type,
                instance_id="obj-1",
                anchor=NativePosition(0, 0, 0),
                rows=None,
                columns=None,
            ),
        ),
    )


def _preview_says_picture(control_type: str) -> bool:
    """hwp_live_preview: does this control make a page count as non-blank?"""
    return hwp_live_preview._page_has_visible_structure(_detailed(control_type))


def _inputs_says_picture(control_type: str) -> bool:
    """hwp_priority_object_inputs: does target.kind="picture" resolve onto it?"""
    resolved = hwp_priority_object_inputs.resolve_control_target(
        hwp_priority_object_inputs.ControlTargetRequest(
            _page(control_type),
            HwpOperateTarget(kind="picture", binding="active"),
            shared.PICTURE_CONTROL_TYPES,
            None,
        )
    )
    return isinstance(resolved, hwp_priority_object_inputs.ResolvedObjectControl)


def _runtime_says_picture(control_type: str) -> bool:
    """hwp_priority_object_runtime: does readback verification see a picture?"""
    return bool(
        hwp_priority_object_runtime._picture_controls(_detailed(control_type).controls)
    )


def _public_tools_says_picture(control_type: str) -> bool:
    """hwp_public_object_tools: does the needs_target rescan offer it?"""
    executor = cast(
        hwp_public_object_tools.PublicActionExecutor,
        SimpleNamespace(
            inspect_page_fast=lambda *_args: _page_coroutine(control_type),
        ),
    )
    tools = HwpPublicObjectTools(executor)
    result = asyncio.run(
        tools._unknown_target("op-1", None, picture_only=True),
    )
    return bool(result.target_candidates)


async def _page_coroutine(control_type: str) -> NativePageInspection:
    return _page(control_type)


PICTURE_PATHS = {
    "hwp_live_preview": _preview_says_picture,
    "hwp_priority_object_inputs": _inputs_says_picture,
    "hwp_priority_object_runtime": _runtime_says_picture,
    "hwp_public_object_tools": _public_tools_says_picture,
}


# --------------------------------------------------------------------------
# 1. One place.  A sixth copy must be impossible to add without failing here.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", OWNED_MODULES)
@pytest.mark.parametrize("token", CONTROL_TYPE_TOKENS)
def test_control_type_tokens_live_only_in_the_shared_module(
    module_name: str,
    token: str,
) -> None:
    source = (SCRIPTS / module_name).read_text(encoding="utf-8")
    hits = re.findall(rf'"{token}"', source)
    assert not hits, (
        f'{module_name} still spells the control type "{token}" itself '
        f"({len(hits)}x); import it from hwp_object_control_types instead"
    )


def test_shared_module_owns_the_tokens() -> None:
    source = (SCRIPTS / "hwp_object_control_types.py").read_text(encoding="utf-8")
    for token in CONTROL_TYPE_TOKENS:
        assert f'"{token}"' in source


@pytest.mark.parametrize(
    "module",
    (
        hwp_live_preview,
        hwp_priority_object_inputs,
        hwp_priority_object_recipes,
        hwp_priority_object_runtime,
        hwp_public_object_tools,
    ),
)
def test_every_module_is_wired_to_the_shared_predicate(module: object) -> None:
    """The deep recipe paths cannot be driven without Hancom; pin the binding.

    ``hwp_priority_object_recipes`` decides picture-vs-table three levels inside
    an async native call chain.  What we can pin without running Hancom is that
    it resolved the *shared* function object rather than re-implementing one.
    """
    bound = [
        name
        for name in (
            "is_control_type",
            "is_picture_control_type",
            "is_table_control_type",
        )
        if getattr(module, name, None) is not None
    ]
    assert bound, f"{module.__name__} imports neither shared control-type predicate"
    for name in bound:
        assert getattr(module, name) is getattr(shared, name)


# --------------------------------------------------------------------------
# 2. One rule.  Every path agrees, for every casing.  This is the core.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("control_type", PICTURE_VALUES)
def test_all_paths_agree_that_picture_types_are_pictures(control_type: str) -> None:
    verdicts = {name: path(control_type) for name, path in PICTURE_PATHS.items()}
    assert all(verdicts.values()), (
        f"paths disagree on control_type={control_type!r}: {verdicts}"
    )


@pytest.mark.parametrize("control_type", TABLE_VALUES + NEITHER_VALUES)
def test_all_paths_agree_that_non_picture_types_are_not_pictures(
    control_type: str,
) -> None:
    verdicts = {name: path(control_type) for name, path in PICTURE_PATHS.items()}
    assert not any(verdicts.values()), (
        f"paths disagree on control_type={control_type!r}: {verdicts}"
    )


# --------------------------------------------------------------------------
# 3. Safety.  Table and picture must never cross, under any casing.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("control_type", TABLE_VALUES)
def test_a_table_is_never_judged_a_picture(control_type: str) -> None:
    assert shared.is_table_control_type(control_type)
    assert not shared.is_picture_control_type(control_type)


@pytest.mark.parametrize("control_type", PICTURE_VALUES)
def test_a_picture_is_never_judged_a_table(control_type: str) -> None:
    assert shared.is_picture_control_type(control_type)
    assert not shared.is_table_control_type(control_type)


def test_table_and_picture_sets_are_disjoint_under_casefolding() -> None:
    folded_tables = {value.casefold() for value in shared.TABLE_CONTROL_TYPES}
    folded_pictures = {value.casefold() for value in shared.PICTURE_CONTROL_TYPES}
    assert not folded_tables & folded_pictures


def test_caption_candidates_are_exactly_table_plus_picture() -> None:
    assert shared.CAPTIONABLE_CONTROL_TYPES == (
        shared.TABLE_CONTROL_TYPES | shared.PICTURE_CONTROL_TYPES
    )


@pytest.mark.parametrize("control_type", TABLE_VALUES)
def test_uppercase_table_reaching_caption_routes_to_the_table_branch(
    control_type: str,
) -> None:
    """The hazard that unifying on casefold would otherwise introduce.

    ``caption.add`` resolves against table-or-picture, then splits on
    ``is_table_control_type``.  Once the candidate set accepts "TBL", the split
    must accept it too -- otherwise a table falls into the *else* branch and is
    captioned as a picture.
    """
    resolved = hwp_priority_object_inputs.resolve_control_target(
        hwp_priority_object_inputs.ControlTargetRequest(
            _page(control_type),
            None,
            shared.CAPTIONABLE_CONTROL_TYPES,
            None,
        )
    )
    assert isinstance(resolved, hwp_priority_object_inputs.ResolvedObjectControl)
    assert shared.is_table_control_type(resolved.control_type)
    assert not shared.is_picture_control_type(resolved.control_type)


# --------------------------------------------------------------------------
# 4. No behaviour change for the preview.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("control_type", "expected"),
    (
        # The preview already casefolded, so these verdicts are its old ones.
        ("gso", True),
        ("GSO", True),
        ("pic", True),
        ("picture", True),
        ("Picture", True),
        ("tbl", False),
        ("cold", False),
        ("secd", False),
        ("pgnp", False),
        ("nwno", False),
        ("atno", False),
        ("$pic", False),
    ),
)
def test_preview_verdict_is_unchanged(control_type: str, expected: bool) -> None:
    assert _preview_says_picture(control_type) is expected


def test_preview_still_reports_blank_pages_as_blank() -> None:
    empty = NativeDetailedInspection(
        document_id=1,
        full_name="C:\\doc.hwp",
        page=1,
        page_count=1,
        text="   \n\t ",
        controls=(),
        cells=(),
        captions=(),
    )
    assert not hwp_live_preview._page_has_visible_structure(empty)


def test_preview_ignores_pictures_anchored_on_another_page() -> None:
    detail = _detailed("gso")
    off_page = NativeDetailedInspection(
        document_id=detail.document_id,
        full_name=detail.full_name,
        page=5,
        page_count=9,
        text="",
        controls=detail.controls,
        cells=(),
        captions=(),
    )
    assert not hwp_live_preview._page_has_visible_structure(off_page)


# --------------------------------------------------------------------------
# 5. The shared constants must themselves be canonical.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "constant",
    ("TABLE_CONTROL_TYPES", "PICTURE_CONTROL_TYPES", "CAPTIONABLE_CONTROL_TYPES"),
)
def test_shared_constants_are_stored_casefolded(constant: str) -> None:
    """Membership is tested as ``value.casefold() in CONSTANT``.

    An uppercase entry in the constant itself would be unreachable, so the
    constants must already be casefolded for the predicate to be total.
    """
    for value in cast(frozenset[str], getattr(shared, constant)):
        assert value == value.casefold()
