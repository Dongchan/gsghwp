from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from hwp_runtime import DocumentAutomationError, PictureControl


class MillimeterConverter(Protocol):
    def MiliToHwpUnit(self, value: float) -> float: ...


class PictureProperties(Protocol):
    def SetItem(self, name: str, value: float | bool) -> None: ...


@runtime_checkable
class ResizablePictureControl(Protocol):
    @property
    def Properties(self) -> PictureProperties: ...

    @Properties.setter
    def Properties(self, value: PictureProperties) -> None: ...


def enforce_picture_size(
    hwp: MillimeterConverter,
    control: PictureControl | None,
    *,
    width_mm: float,
    height_mm: float,
    guard: Callable[[], None] | None = None,
) -> None:
    def check_context() -> None:
        if guard is not None:
            guard()

    check_context()
    if control is None or not isinstance(control, ResizablePictureControl):
        raise DocumentAutomationError("삽입한 그림의 크기를 확정할 수 없습니다")
    check_context()
    properties = control.Properties
    check_context()
    width = hwp.MiliToHwpUnit(width_mm)
    check_context()
    properties.SetItem("Width", width)
    check_context()
    height = hwp.MiliToHwpUnit(height_mm)
    check_context()
    properties.SetItem("Height", height)
    check_context()
    properties.SetItem("TreatAsChar", True)
    check_context()
    control.Properties = properties
    check_context()
