from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol, assert_never

from typing_extensions import TypeIs

from hwp_runtime import DocumentAutomationError
from manifest_contract import PageRecord

SAFE_MARGIN_MM: Final = 5.0


@dataclass(frozen=True, slots=True)
class ContentBox:
    width_mm: float
    height_mm: float


@dataclass(frozen=True, slots=True)
class PageGeometry:
    short_mm: float
    long_mm: float
    landscape: bool
    content: ContentBox


class ParameterSet(Protocol):
    def SetItem(self, name: str, value: int) -> None: ...


class PageDefinition(Protocol):
    PaperWidth: float
    PaperHeight: float
    Landscape: bool
    LeftMargin: float
    RightMargin: float
    TopMargin: float
    BottomMargin: float
    HeaderLen: float
    FooterLen: float
    GutterLen: float


class SectionDefinition(Protocol):
    @property
    def HSet(self) -> ParameterSet: ...

    @property
    def PageDef(self) -> PageDefinition: ...


class HwpParameterSets(Protocol):
    @property
    def HSecDef(self) -> SectionDefinition: ...


class HwpAction(Protocol):
    def Run(self, name: str) -> bool: ...

    def GetDefault(self, name: str, parameters: ParameterSet) -> bool: ...

    def Execute(self, name: str, parameters: ParameterSet) -> bool: ...


class PageSetupHwp(Protocol):
    @property
    def HAction(self) -> HwpAction: ...

    @property
    def HParameterSet(self) -> HwpParameterSets: ...

    def MiliToHwpUnit(self, value: float) -> float: ...


def _is_known_orientation(
    value: str,
) -> TypeIs[Literal["landscape", "portrait", "square"]]:
    return value in ("landscape", "portrait", "square")


def page_geometry(page: PageRecord) -> PageGeometry:
    width_mm = page.width_pt * 25.4 / 72.0
    height_mm = page.height_pt * 25.4 / 72.0
    short_mm = min(width_mm, height_mm)
    long_mm = max(width_mm, height_mm)
    match page.orientation:
        case _ as unreachable if not _is_known_orientation(unreachable):
            assert_never(unreachable)
        case "landscape":
            landscape = True
            oriented_width, oriented_height = long_mm, short_mm
        case "portrait" | "square":
            landscape = False
            oriented_width, oriented_height = short_mm, long_mm
    content = ContentBox(
        width_mm=oriented_width - 2.0 * SAFE_MARGIN_MM,
        height_mm=oriented_height - 2.0 * SAFE_MARGIN_MM,
    )
    if content.width_mm <= 0.0 or content.height_mm <= 0.0:
        raise DocumentAutomationError(f"페이지 내용 영역이 없습니다: {page.number}")
    return PageGeometry(short_mm, long_mm, landscape, content)


def apply_page_setup(hwp: PageSetupHwp, geometry: PageGeometry) -> None:
    section = hwp.HParameterSet.HSecDef
    if not hwp.HAction.GetDefault("PageSetup", section.HSet):
        raise DocumentAutomationError("PageSetup 기본값을 읽지 못했습니다")
    section.HSet.SetItem("ApplyTo", 2)
    page = section.PageDef
    page.PaperWidth = hwp.MiliToHwpUnit(geometry.short_mm)
    page.PaperHeight = hwp.MiliToHwpUnit(geometry.long_mm)
    page.Landscape = geometry.landscape
    margin = hwp.MiliToHwpUnit(SAFE_MARGIN_MM)
    page.LeftMargin = margin
    page.RightMargin = margin
    page.TopMargin = margin
    page.BottomMargin = margin
    zero = hwp.MiliToHwpUnit(0.0)
    page.HeaderLen = zero
    page.FooterLen = zero
    page.GutterLen = zero
    if not hwp.HAction.Execute("PageSetup", section.HSet):
        raise DocumentAutomationError("PageSetup 적용에 실패했습니다")
