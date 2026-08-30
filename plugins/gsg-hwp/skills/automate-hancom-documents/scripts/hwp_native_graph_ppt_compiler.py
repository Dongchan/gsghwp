from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Literal

from hwp_native_graph_document_compiler import (
    CompileError,
    CompileErrorCode,
    ControlIntent,
    DocumentIntent,
    ImageIntent,
    PageSetup,
    ParagraphIntent,
    RunIntent,
    SectionIntent,
    StoryIntent,
    TableIntent,
    compile_document_intent,
)
from hwp_office_pptx import (
    PptxImageShapeData,
    PptxSlideContent,
    PptxTableShapeData,
    PptxTextShapeData,
    read_pptx_slide,
)


class PptCompileErrorCode(StrEnum):
    MALFORMED_SOURCE = "PPT_MALFORMED"
    MISSING_IMAGE = "PPT_MISSING_IMAGE"
    BLANK_SOURCE = "PPT_BLANK"


class PptCompileError(ValueError):
    code: PptCompileErrorCode

    def __init__(self, code: PptCompileErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RasterPrimitive:
    kind: Literal["text", "image", "table", "group"]
    text: str = ""
    rows: int = 0
    columns: int = 0
    asset_digest: str | None = None
    left: float = 0.0
    top: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass(frozen=True, slots=True)
class RasterPage:
    primitives: tuple[RasterPrimitive, ...]
    page_width: float | None = None
    page_height: float | None = None
    scale: float | None = None


def _page_setup(width_emu: int, height_emu: int) -> PageSetup:
    return PageSetup(
        paper_width_mm=max(1, width_emu // 36000),
        paper_height_mm=max(1, height_emu // 36000),
        orientation="landscape" if width_emu > height_emu else "portrait",
        observed=True,
    )


def _text_paragraphs(shape: PptxTextShapeData) -> tuple[ParagraphIntent, ...]:
    paragraphs: list[ParagraphIntent] = []
    for paragraph in shape.paragraphs:
        runs = tuple(RunIntent(run.text) for run in paragraph.runs if run.text)
        if runs:
            paragraphs.append(ParagraphIntent(runs=runs))
    return tuple(paragraphs)


def compile_slide_content(
    slide: PptxSlideContent,
    *,
    assets: dict[str, bytes] | None = None,
) -> DocumentIntent:
    paragraphs: list[ParagraphIntent] = []
    tables: list[TableIntent] = []
    images: list[ImageIntent] = []
    controls: list[ControlIntent] = []
    known_assets = assets or {}
    for element in slide.elements:
        if isinstance(element, PptxTextShapeData):
            paragraphs.extend(_text_paragraphs(element))
        elif isinstance(element, PptxTableShapeData):
            tables.append(
                TableIntent(
                    rows=len(element.rows),
                    columns=len(element.rows[0]) if element.rows else 0,
                )
            )
        elif isinstance(element, PptxImageShapeData):
            digest = sha256(element.source_name.encode("utf-8")).hexdigest()
            if digest not in known_assets and not element.path.exists():
                raise PptCompileError(
                    PptCompileErrorCode.MISSING_IMAGE,
                    "image asset is missing before target creation",
                )
            images.append(ImageIntent(digest))
        else:
            controls.append(ControlIntent("group"))
    if not slide.elements and not slide.unsupported_elements:
        raise PptCompileError(
            PptCompileErrorCode.BLANK_SOURCE, "blank slide has no primitives"
        )
    for unsupported in slide.unsupported_elements:
        if unsupported == "group_shape":
            controls.append(ControlIntent("group"))
    return DocumentIntent(
        page_setup=_page_setup(slide.slide_width_emu, slide.slide_height_emu),
        sections=(
            SectionIntent(
                stories=(
                    StoryIntent(
                        role="body",
                        paragraphs=tuple(paragraphs),
                        tables=tuple(tables),
                        images=tuple(images),
                        controls=tuple(controls),
                    ),
                )
            ),
        ),
    )


def compile_pptx_slide(
    path: Path,
    *,
    slide_number: int,
    output_directory: Path,
    assets: dict[str, bytes] | None = None,
) -> DocumentIntent:
    if slide_number < 1:
        raise PptCompileError(
            PptCompileErrorCode.MALFORMED_SOURCE, "slide number must be >= 1"
        )
    try:
        slide = read_pptx_slide(
            path, slide_number=slide_number, output_directory=output_directory
        )
    except Exception as error:
        raise PptCompileError(
            PptCompileErrorCode.MALFORMED_SOURCE, str(error)
        ) from error
    return compile_slide_content(slide, assets=assets)


def compile_raster_page(page: RasterPage) -> DocumentIntent:
    if not page.primitives:
        raise PptCompileError(
            PptCompileErrorCode.BLANK_SOURCE, "raster page has no primitives"
        )
    paragraphs: list[ParagraphIntent] = []
    tables: list[TableIntent] = []
    images: list[ImageIntent] = []
    controls: list[ControlIntent] = []
    for primitive in page.primitives:
        if primitive.kind == "text":
            paragraphs.append(ParagraphIntent(runs=(RunIntent(primitive.text),)))
        elif primitive.kind == "table":
            tables.append(TableIntent(primitive.rows, primitive.columns))
        elif primitive.kind == "image":
            if not primitive.asset_digest:
                raise PptCompileError(
                    PptCompileErrorCode.MISSING_IMAGE,
                    "image asset is missing before target creation",
                )
            images.append(ImageIntent(primitive.asset_digest))
        else:
            controls.append(ControlIntent("group"))
    width = int(page.page_width or 210)
    height = int(page.page_height or 297)
    if page.scale is not None:
        width = max(1, int(width * page.scale))
        height = max(1, int(height * page.scale))
    return DocumentIntent(
        page_setup=PageSetup(
            width, height, "portrait", observed=page.page_width is not None
        ),
        sections=(
            SectionIntent(
                stories=(
                    StoryIntent(
                        role="body",
                        paragraphs=tuple(paragraphs),
                        tables=tuple(tables),
                        images=tuple(images),
                        controls=tuple(controls),
                    ),
                )
            ),
        ),
    )


def compile_and_build(intent: DocumentIntent):
    if intent.page_setup is None or not intent.page_setup.observed:
        raise CompileError(
            CompileErrorCode.PAGE_SETUP_REQUIRED
            if intent.page_setup is None
            else CompileErrorCode.UNVERIFIED_DEFAULT,
            "page setup must be observed before Hancom mutation",
        )
    return compile_document_intent(intent)


__all__ = [
    "PptCompileError",
    "PptCompileErrorCode",
    "RasterPage",
    "RasterPrimitive",
    "compile_and_build",
    "compile_pptx_slide",
    "compile_raster_page",
    "compile_slide_content",
]
