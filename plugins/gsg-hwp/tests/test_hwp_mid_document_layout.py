from __future__ import annotations

import sys
from functools import partial
from pathlib import Path
from typing import final

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

import hwp_public_action_metadata as metadata  # noqa: E402
from hwp_layout_preflight import LayoutPreflightResult  # noqa: E402
from hwp_live_contract import LayoutPlan, ParagraphBlock  # noqa: E402
from hwp_live_native_action_commands import (  # noqa: E402
    InsertTextCommand,
    MovePageCommand,
    RunCommand,
)
from hwp_live_native_layout import (  # noqa: E402
    NativeLayoutContext,
    build_native_layout_request,
)
from hwp_operation_contract import (  # noqa: E402
    HwpOperateGuards,
    HwpOperateInputs,
    OperationResult,
)
from hwp_public_document_tools import HwpPublicDocumentTools  # noqa: E402


def _paragraph_plan(*, target: str, page: int | None = None) -> LayoutPlan:
    return LayoutPlan.model_validate(
        {
            "target": target,
            "page": page,
            "blocks": ({"kind": "paragraph", "text": "중간 삽입 확인"},),
        }
    )


def test_after_page_layout_requires_an_explicit_page() -> None:
    with pytest.raises(ValidationError, match="after_page.*page"):
        _ = _paragraph_plan(target="after_page")


def test_non_page_layout_rejects_a_page_number() -> None:
    with pytest.raises(ValidationError, match="page.*after_page"):
        _ = _paragraph_plan(target="current", page=8)


def test_after_page_layout_inserts_before_preserved_following_page() -> None:
    plan = _paragraph_plan(target="after_page", page=8)
    request = build_native_layout_request(
        NativeLayoutContext(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            style_ids=(),
            page_count=20,
        ),
        plan,
        {},
    )

    assert request.commands == (
        MovePageCommand(9),
        RunCommand("MovePageBegin"),
        RunCommand("BreakPage"),
        MovePageCommand(9),
        RunCommand("MovePageBegin"),
        InsertTextCommand("중간 삽입 확인"),
    )


def test_after_last_page_layout_appends_with_only_one_page_break() -> None:
    plan = _paragraph_plan(target="after_page", page=8)
    request = build_native_layout_request(
        NativeLayoutContext(
            document_id=17,
            full_name="C:/documents/sample.hwp",
            style_ids=(),
            page_count=8,
        ),
        plan,
        {},
    )

    assert request.commands == (
        MovePageCommand(8),
        RunCommand("MovePageEnd"),
        RunCommand("BreakPage"),
        InsertTextCommand("중간 삽입 확인"),
    )


def test_expanding_image_frames_preserves_after_page_target() -> None:
    plan = _paragraph_plan(target="after_page", page=8)

    expanded = plan.expand_image_frames(165.0)

    assert expanded.target == "after_page"
    assert expanded.page == 8


@final
class _CapturingExecutor:
    def __init__(self) -> None:
        self.intent: str = ""
        self.inputs: HwpOperateInputs | None = None

    async def preflight_layout(
        self,
        document_selector: str | None,
        plan: LayoutPlan,
    ) -> LayoutPreflightResult:
        _ = document_selector, plan
        return LayoutPreflightResult(
            usable_width_mm=180.0,
            usable_height_mm=250.0,
            estimated_width_mm=0.0,
            estimated_height_mm=0.0,
            overflow="none",
            safe_to_write=True,
        )

    async def execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationResult:
        _ = guards
        self.intent = intent
        self.inputs = inputs
        return OperationResult(
            status="executed",
            changed=True,
            query=intent,
            registry_entries=1,
            lookup_microseconds=0,
            message="ok",
            verified=True,
            commands_executed=1,
            modified=True,
            retry_safe=True,
        )


def test_insert_layout_preserves_middle_document_target() -> None:
    executor = _CapturingExecutor()
    tools = HwpPublicDocumentTools(executor)
    plan = _paragraph_plan(target="after_page", page=8)

    _ = anyio.run(
        partial(
            tools.hwp_insert_layout,
            operation_id="insert-middle-layout",
            layout=plan,
        )
    )

    assert executor.intent == metadata.INSERT_LAYOUT_INTENT
    assert executor.inputs is not None
    assert executor.inputs.layout == plan
    assert executor.inputs.policy.atomic is False


def test_append_layout_still_forces_document_end() -> None:
    executor = _CapturingExecutor()
    tools = HwpPublicDocumentTools(executor)
    plan = LayoutPlan(
        target="current",
        blocks=(ParagraphBlock(kind="paragraph", text="끝 삽입 확인"),),
    )

    _ = anyio.run(
        partial(
            tools.hwp_append_layout,
            operation_id="append-end-layout",
            layout=plan,
        )
    )

    assert executor.inputs is not None
    assert executor.inputs.layout is not None
    assert executor.inputs.layout.target == "document_end"
    assert executor.inputs.layout.page is None
