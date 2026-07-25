from __future__ import annotations

from hwp_live_contract import LayoutPlan
from hwp_operation_contract import OperationResult, OperationStatus
from hwp_operation_registry import operation_registry


DOCUMENT_END_LAYOUT_RECIPE_ID = "recipe:document_end_layout"
INSERT_LAYOUT_RECIPE_ID = "recipe:document.insert_layout.v1"
_DOCUMENT_END_LAYOUT_ALIASES = frozenset(
    {
        DOCUMENT_END_LAYOUT_RECIPE_ID,
        "문서 끝에 표 추가",
        "문서 마지막에 표 추가",
        "문서 끝에 레이아웃 추가",
        "문서 마지막에 레이아웃 추가",
        "add table at document end",
        "append table",
    }
)


def _compact(value: str) -> str:
    return "".join(
        character.casefold()
        for character in value
        if character.isalnum() or character == ":"
    )


def is_document_end_layout_intent(value: str) -> bool:
    compact = _compact(value.strip())
    return any(compact == _compact(alias) for alias in _DOCUMENT_END_LAYOUT_ALIASES)


def layout_operation_result(
    query: str,
    status: OperationStatus,
    message: str,
    lookup_microseconds: int,
    plan: LayoutPlan | None,
) -> OperationResult:
    target = "document_end" if plan is None else plan.target
    if target == "document_end":
        recipe_id = DOCUMENT_END_LAYOUT_RECIPE_ID
        recipe_steps = ("MoveDocEnd", "ApplyLayout")
    elif target == "after_page":
        recipe_id = INSERT_LAYOUT_RECIPE_ID
        recipe_steps = ("MovePage", "MovePageEnd", "BreakPage", "ApplyLayout")
    else:
        recipe_id = INSERT_LAYOUT_RECIPE_ID
        recipe_steps = ("ApplyLayout",)
    return OperationResult(
        status=status,
        query=query,
        registry_entries=operation_registry().count,
        lookup_microseconds=lookup_microseconds,
        message=message,
        recipe_id=recipe_id,
        recipe_steps=recipe_steps,
    )
