from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Callable


class LayoutConstraint(StrEnum):
    PAGE_OVERFLOW = "page_overflow"
    TABLE_SPLIT = "table_split"
    QUANTIZED = "quantized"
    UNAVAILABLE = "unavailable"


class ConvergenceStatus(StrEnum):
    STABLE = "stable"
    CORRECTED = "corrected"
    OSCILLATING = "oscillating"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    CONTRADICTION = "contradiction"


@dataclass(frozen=True, slots=True)
class LayoutFacts:
    page_count: int
    overflow: bool
    table_splits: int
    quantized: bool
    available: bool = True
    generation: int = 0


@dataclass(frozen=True, slots=True)
class LayoutWorld:
    facts: LayoutFacts
    patches: tuple[str, ...] = ()
    rolled_back: bool = False
    render_calls: int = 0


@dataclass(frozen=True, slots=True)
class ConvergenceReceipt:
    status: ConvergenceStatus
    iterations: int
    patches: tuple[str, ...]
    facts: LayoutFacts
    diagnostic: str
    rolled_back: bool
    render_calls: int


class RecalcTimeout(TimeoutError):
    pass


RecalcWaiter = Callable[[int], LayoutFacts]
Mutator = Callable[[LayoutWorld, str], LayoutWorld]


def _correction_for(
    facts: LayoutFacts, constraints: frozenset[LayoutConstraint]
) -> str | None:
    if LayoutConstraint.UNAVAILABLE in constraints or not facts.available:
        return None
    if LayoutConstraint.PAGE_OVERFLOW in constraints and facts.overflow:
        return "shrink-page-overflow"
    if LayoutConstraint.TABLE_SPLIT in constraints and facts.table_splits > 0:
        return "join-table-split"
    if LayoutConstraint.QUANTIZED in constraints and facts.quantized:
        return "round-native-quantum"
    return None


def converge_layout(
    world: LayoutWorld,
    *,
    constraints: frozenset[LayoutConstraint],
    wait_recalc: RecalcWaiter,
    mutate: Mutator,
    expected_generation: int,
    max_iterations: int = 8,
) -> tuple[LayoutWorld, ConvergenceReceipt]:
    current = world
    applied: list[str] = []
    seen: set[tuple[int, bool, int, bool]] = set()
    try:
        facts = wait_recalc(expected_generation)
    except RecalcTimeout:
        return current, ConvergenceReceipt(
            status=ConvergenceStatus.TIMEOUT,
            iterations=0,
            patches=(),
            facts=current.facts,
            diagnostic="native recalc event did not complete",
            rolled_back=False,
            render_calls=current.render_calls,
        )
    current = replace(current, facts=facts)
    if not facts.available or LayoutConstraint.UNAVAILABLE in constraints:
        return current, ConvergenceReceipt(
            status=ConvergenceStatus.UNAVAILABLE,
            iterations=0,
            patches=(),
            facts=facts,
            diagnostic="declared native layout fact is unavailable",
            rolled_back=False,
            render_calls=current.render_calls,
        )
    if (
        LayoutConstraint.PAGE_OVERFLOW in constraints
        and LayoutConstraint.TABLE_SPLIT in constraints
    ):
        if facts.overflow and facts.table_splits == 0:
            blocked = replace(current, rolled_back=True)
            return blocked, ConvergenceReceipt(
                status=ConvergenceStatus.CONTRADICTION,
                iterations=0,
                patches=(),
                facts=facts,
                diagnostic="page overflow cannot be corrected without a table split fact",
                rolled_back=True,
                render_calls=current.render_calls,
            )
    for iteration in range(max_iterations):
        key = (facts.page_count, facts.overflow, facts.table_splits, facts.quantized)
        patch = _correction_for(facts, constraints)
        if key in seen and patch is not None:
            blocked = replace(current, rolled_back=True)
            return blocked, ConvergenceReceipt(
                status=ConvergenceStatus.OSCILLATING,
                iterations=iteration,
                patches=tuple(applied),
                facts=facts,
                diagnostic="correction sequence repeated",
                rolled_back=True,
                render_calls=current.render_calls,
            )
        seen.add(key)
        if patch is None:
            status = (
                ConvergenceStatus.STABLE if not applied else ConvergenceStatus.CORRECTED
            )
            return current, ConvergenceReceipt(
                status=status,
                iterations=iteration if applied else 0,
                patches=tuple(applied),
                facts=facts,
                diagnostic="constraints satisfied",
                rolled_back=False,
                render_calls=current.render_calls,
            )
        current = mutate(current, patch)
        applied.append(patch)
        try:
            facts = wait_recalc(current.facts.generation)
        except RecalcTimeout:
            return current, ConvergenceReceipt(
                status=ConvergenceStatus.TIMEOUT,
                iterations=iteration + 1,
                patches=tuple(applied),
                facts=current.facts,
                diagnostic="native recalc event did not complete",
                rolled_back=False,
                render_calls=current.render_calls,
            )
        current = replace(current, facts=facts)
    blocked = replace(current, rolled_back=True)
    return blocked, ConvergenceReceipt(
        status=ConvergenceStatus.OSCILLATING,
        iterations=max_iterations,
        patches=tuple(applied),
        facts=current.facts,
        diagnostic="exceeded bounded correction budget",
        rolled_back=True,
        render_calls=current.render_calls,
    )


def save_reopen_equal(before: LayoutFacts, after: LayoutFacts) -> bool:
    return (
        before.page_count == after.page_count
        and before.overflow == after.overflow
        and before.table_splits == after.table_splits
        and before.quantized == after.quantized
    )


__all__ = [
    "ConvergenceReceipt",
    "ConvergenceStatus",
    "LayoutConstraint",
    "LayoutFacts",
    "LayoutWorld",
    "RecalcTimeout",
    "converge_layout",
    "save_reopen_equal",
]
