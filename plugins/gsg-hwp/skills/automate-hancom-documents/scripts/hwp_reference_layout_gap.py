from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from hwp_live_values import ContractModel


GapAxis = Literal["row", "column"]


class ProtectedGap(ContractModel):
    axis: GapAxis
    top: int = Field(ge=0, le=49)
    left: int = Field(ge=0, le=49)
    bottom: int = Field(ge=1, le=50)
    right: int = Field(ge=1, le=50)

    @model_validator(mode="after")
    def validate_rectangle(self) -> ProtectedGap:
        if self.top >= self.bottom or self.left >= self.right:
            raise ValueError("protected gap must be a non-empty rectangle")
        return self


def coalesce_protected_gaps(
    gaps: list[ProtectedGap],
) -> tuple[ProtectedGap, ...]:
    ordered = sorted(
        gaps,
        key=lambda gap: (
            gap.axis,
            gap.left,
            gap.right,
            gap.top,
            gap.bottom,
        ),
    )
    merged: list[ProtectedGap] = []
    for gap in ordered:
        if not merged:
            merged.append(gap)
            continue
        previous = merged[-1]
        joins_column = (
            gap.axis == previous.axis == "column"
            and gap.left == previous.left
            and gap.right == previous.right
            and gap.top == previous.bottom
        )
        joins_row = (
            gap.axis == previous.axis == "row"
            and gap.top == previous.top
            and gap.bottom == previous.bottom
            and gap.left == previous.right
        )
        if joins_column:
            merged[-1] = previous.model_copy(
                update={"bottom": gap.bottom}
            )
        elif joins_row:
            merged[-1] = previous.model_copy(
                update={"right": gap.right}
            )
        else:
            merged.append(gap)
    return tuple(merged)


def protected_gap_minimums(
    gaps: tuple[ProtectedGap, ...],
    row_sizes: tuple[int, ...],
    column_sizes: tuple[int, ...],
) -> tuple[int, ...]:
    return tuple(
        sum(row_sizes[gap.top : gap.bottom])
        if gap.axis == "row"
        else sum(column_sizes[gap.left : gap.right])
        for gap in gaps
    )
