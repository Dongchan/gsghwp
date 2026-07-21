from __future__ import annotations

from dataclasses import dataclass
from typing import Final, final

from hwp_live_structure_contract import StructureCell


CIRCLED_NUMBERS: Final = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
IMAGE_WIDTH_MM: Final = 84.0
IMAGE_HEIGHT_MM: Final = 63.0


@final
class VisibilitySeriesPlanError(Exception):
    __slots__ = ("message",)

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class VisibilityRecord:
    number: int
    category: str
    location: str
    distance: str
    elevation: str

    @property
    def label(self) -> str:
        return f"예비조망점 {CIRCLED_NUMBERS[self.number - 1]}"

    @property
    def result(self) -> str:
        return (
            f"{self.location}에서 사업대상지는 약 {self.distance} 이격되어 있으며, "
            f"{self.category} 조망점의 가시권 및 경관 변화를 분석함."
        )


@dataclass(frozen=True, slots=True)
class VisibilitySlot:
    header: StructureCell
    category: StructureCell
    label: StructureCell
    location: StructureCell
    distance: StructureCell
    elevation: StructureCell
    visibility_image: StructureCell
    current_image: StructureCell
    result: StructureCell
