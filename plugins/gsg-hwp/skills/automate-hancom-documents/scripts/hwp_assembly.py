from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, assert_never, runtime_checkable

from PIL import Image
from pydantic import ValidationError
from typing_extensions import TypeIs

from hwp_runtime import (
    DocumentAutomationError,
    HwpSession,
    PictureControl,
    output_document,
)
from hwp_page_setup import (
    SAFE_MARGIN_MM,
    ContentBox,
    PageGeometry,
    PageSetupHwp,
    apply_page_setup,
    page_geometry,
)
from hwp_picture_placement import enforce_picture_size
from manifest_contract import ManifestV2, PageRecord

__all__ = (
    "ContentBox",
    "PageGeometry",
    "SAFE_MARGIN_MM",
    "assemble_hybrid",
    "fit_raster",
    "load_assembly_plan",
    "normalize_hwp_text",
    "write_assembly",
)


@dataclass(frozen=True, slots=True)
class RasterSize:
    width_mm: float
    height_mm: float


@dataclass(frozen=True, slots=True)
class RasterPlacement:
    path: Path
    width_mm: float
    height_mm: float


@dataclass(frozen=True, slots=True)
class RasterPage:
    number: int
    geometry: PageGeometry
    raster: RasterPlacement


@dataclass(frozen=True, slots=True)
class TextPage:
    number: int
    geometry: PageGeometry
    text: str


type AssemblyPage = RasterPage | TextPage


def _is_known_page_mode(value: str) -> TypeIs[Literal["editable", "hybrid", "raster"]]:
    return value in ("editable", "hybrid", "raster")


def _is_known_page(value: AssemblyPage | None) -> TypeIs[RasterPage | TextPage]:
    return isinstance(value, (RasterPage, TextPage))


@dataclass(frozen=True, slots=True)
class AssemblyPlan:
    pages: tuple[AssemblyPage, ...]


class AssemblyWriterHwp(PageSetupHwp, Protocol):
    def insert_picture(
        self,
        path: str,
        treat_as_char: bool = True,
        embedded: bool = True,
        sizeoption: int = 0,
        reverse: bool = False,
        watermark: bool = False,
        effect: int = 0,
        width: float = 0.0,
        height: float = 0.0,
    ) -> PictureControl | None: ...

    def ParagraphShapeAlignLeft(self) -> bool: ...

    def insert_text(self, text: str) -> bool: ...


@runtime_checkable
class AssemblyHwp(AssemblyWriterHwp, Protocol):
    def save_as(
        self,
        path: str,
        format: str = "HWP",
        arg: str = "",
        split_page: bool = False,
    ) -> bool: ...


def fit_raster(image_size: tuple[int, int], content: ContentBox) -> RasterSize:
    width_px, height_px = image_size
    if width_px <= 0 or height_px <= 0:
        raise DocumentAutomationError("래스터 이미지 크기는 양수여야 합니다")
    if content.width_mm <= 0.0 or content.height_mm <= 0.0:
        raise DocumentAutomationError("페이지 내용 영역 크기는 양수여야 합니다")
    scale = min(content.width_mm / width_px, content.height_mm / height_px)
    return RasterSize(width_mm=width_px * scale, height_mm=height_px * scale)


def _asset(base: Path, relative: str) -> Path:
    resolved = (base / relative).resolve()
    try:
        _ = resolved.relative_to(base)
    except ValueError as error:
        raise DocumentAutomationError(f"manifest 밖의 파일 경로: {relative}") from error
    if not resolved.is_file():
        raise DocumentAutomationError(f"manifest 파일이 없습니다: {resolved}")
    return resolved


def normalize_hwp_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs: list[str] = []
    for block in normalized.split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if len(lines) > 1 and len(lines[0]) <= 60 and not lines[0].endswith((".", ",")):
            paragraphs.extend((lines[0], " ".join(lines[1:])))
        else:
            paragraphs.append(" ".join(lines))
    return "\r\n\r\n".join(paragraphs)


def _raster_page(page: PageRecord, base: Path) -> RasterPage:
    path = _asset(base, page.raster_file)
    geometry = page_geometry(page)
    try:
        with Image.open(path) as image:
            fitted = fit_raster(image.size, geometry.content)
    except OSError as error:
        raise DocumentAutomationError(f"래스터 이미지를 읽을 수 없습니다: {path}") from error
    return RasterPage(
        number=page.number,
        geometry=geometry,
        raster=RasterPlacement(path, fitted.width_mm, fitted.height_mm),
    )


def _text_page(page: PageRecord, base: Path, relative: str) -> TextPage:
    path = _asset(base, relative)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise DocumentAutomationError(f"텍스트 파일을 읽을 수 없습니다: {path}") from error
    return TextPage(page.number, page_geometry(page), normalize_hwp_text(text))


def load_assembly_plan(manifest_path: Path) -> AssemblyPlan:
    source = manifest_path.expanduser().resolve()
    try:
        manifest = ManifestV2.model_validate_json(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        raise DocumentAutomationError(f"manifest 오류 ({source}): {error}") from error
    pages: list[AssemblyPage] = []
    for page in manifest.pages:
        match page.mode:
            case _ as unreachable if not _is_known_page_mode(unreachable):
                assert_never(unreachable)
            case "editable" if page.text_file is not None:
                pages.append(_text_page(page, source.parent, page.text_file))
            case "editable" | "hybrid" | "raster":
                pages.append(_raster_page(page, source.parent))
    return AssemblyPlan(tuple(pages))


def write_assembly(hwp: AssemblyWriterHwp, plan: AssemblyPlan) -> None:
    for index, page in enumerate(plan.pages):
        if index and not hwp.HAction.Run("BreakSection"):
            raise DocumentAutomationError(f"구역 나누기 실패: {page.number}")
        apply_page_setup(hwp, page.geometry)
        match page:
            case _ as unreachable if not _is_known_page(unreachable):
                assert_never(unreachable)
            case RasterPage(raster=raster):
                control = hwp.insert_picture(
                    str(raster.path),
                    treat_as_char=True,
                    embedded=True,
                    sizeoption=1,
                    width=raster.width_mm,
                    height=raster.height_mm,
                )
                enforce_picture_size(
                    hwp,
                    control,
                    width_mm=raster.width_mm,
                    height_mm=raster.height_mm,
                )
            case TextPage(text=text):
                _ = hwp.ParagraphShapeAlignLeft()
                if not hwp.insert_text(text):
                    raise DocumentAutomationError(f"텍스트 삽입 실패: {page.number}")


def assemble_hybrid(manifest_path: Path, output: Path, *, visible: bool) -> Path:
    plan = load_assembly_plan(manifest_path)
    destination = output_document(output)
    with HwpSession(visible=visible) as raw_hwp:
        if not isinstance(raw_hwp, AssemblyHwp):
            raise DocumentAutomationError("HWP 조립 API를 사용할 수 없습니다")
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_assembly(raw_hwp, plan)
        if not raw_hwp.save_as(
            str(destination), format=destination.suffix[1:].upper()
        ):
            raise DocumentAutomationError(f"혼합형 HWP 저장 실패: {destination}")
    return destination
