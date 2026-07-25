from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar


_Candidate = TypeVar("_Candidate")


def highest_fitting_integer(
    low: int,
    high: int,
    build: Callable[[int], _Candidate],
    fits: Callable[[_Candidate], bool],
) -> int | None:
    if not fits(build(low)):
        return None
    result = low
    while low <= high:
        middle = (low + high) // 2
        if fits(build(middle)):
            result = middle
            low = middle + 1
        else:
            high = middle - 1
    return result
