from __future__ import annotations

from typing import Protocol, cast

from pywintypes import com_error

from hwp_live_api import LiveHwpApplication
from hwp_picture_edit_geometry import PictureOriginalSize, PictureSkip


# 한/글 COM 의 Ctrl 개체에는 Properties 가 있지만 HwpControl 선언에는 없다
# (hwp_live_api.py:21-29). hwp_live_native_format_target.py:281-284 가 쓰는
# 방식 그대로, 여기서 읽는 것만 좁게 적어 두고 그 이름으로 본다.
class _PropertySet(Protocol):
    def Item(self, name: str) -> object: ...


class _PicturePropertyReader(Protocol):
    @property
    def CtrlID(self) -> str: ...

    @property
    def Properties(self) -> _PropertySet: ...

    def GetCtrlInstID(self) -> str: ...


_READ_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError, com_error)
_SKIP_NAMES = ("SkipLeft", "SkipTop", "SkipRight", "SkipBottom")


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def read_picture_geometry(
    hwp: LiveHwpApplication,
    instance_id: str,
) -> tuple[PictureOriginalSize, PictureSkip, int | None, int | None] | None:
    """그림 개체의 원본 크기·자르기·상자 크기를 COM 으로 읽는다.

    네이티브 브리지에는 이 값을 내주는 명령이 없다 (ActionProtocol.cpp 의 명령
    어휘에 속성 읽기가 없다). 자르기 비율을 한/글이 쓰는 HWPUNIT 으로 바꾸려면
    OriginalSizeX/OriginalSizeY 가 반드시 필요하므로 여기서 COM 으로 읽는다.

    읽지 못하면 ``None``. 없다는 답도 답이다 -- 호출부가 판단한다.
    """
    try:
        controls = tuple(hwp.ctrl_list)
    except _READ_ERRORS:
        return None
    for control in controls:
        reader = cast(_PicturePropertyReader, cast(object, control))
        try:
            if str(reader.CtrlID).strip() != "gso":
                continue
            if str(reader.GetCtrlInstID()).strip() != instance_id:
                continue
            properties = reader.Properties
            attribute = cast(object, properties.Item("ShapeDrawImageAttr"))
            if attribute is None:
                return None
            image = cast(_PropertySet, attribute)
            original_x = _integer(image.Item("OriginalSizeX"))
            original_y = _integer(image.Item("OriginalSizeY"))
            if original_x is None or original_y is None:
                return None
            skips = tuple(_integer(image.Item(name)) or 0 for name in _SKIP_NAMES)
            return (
                PictureOriginalSize(original_x, original_y),
                PictureSkip(skips[0], skips[1], skips[2], skips[3]),
                _integer(properties.Item("Width")),
                _integer(properties.Item("Height")),
            )
        except _READ_ERRORS:
            return None
    return None
