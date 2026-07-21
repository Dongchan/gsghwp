#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "Pillow==12.2.0",
#     "pdfplumber==0.11.9",
#     "pydantic==2.12.5",
#     "rich==14.3.2",
#     "typer==0.23.1",
# ]
# ///

# ─── How to run ───
# 1. Install uv through your organization's approved package source.
# 2. Analyze and render selected PDF pages:
#      uv run pdf_hybrid.py analyze source.pdf analysis-output --pages 1-3,7
# ──────────────────

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Annotated, Final, final, override

import pdfplumber
import typer
from pdfplumber.page import Page
from rich.console import Console
from typer.models import OptionInfo

from manifest_contract import (
    ManifestTimings,
    ManifestV2,
    ModeSummary,
    Orientation,
    PageMode,
    PageRecord,
    PageSelectionError,
    contiguous_ranges,
    resolve_page_selection,
)
from pdf_rendering import (
    CropTarget,
    RenderPlan,
    crop_media as _crop_media,
    find_pdftoppm,
    image_boxes,
    render_pdf_batches,
)

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()

_DPI_OPTION: Final[OptionInfo] = OptionInfo(
    default=..., param_decls=(), min=96, max=600
)
_PAGES_OPTION: Final[OptionInfo] = OptionInfo(
    default=...,
    param_decls=(),
    help="원본 1-based 쪽 선택, 예: 1-3,7",
)


@final
class PdfHybridError(RuntimeError):
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class PageMetrics:
    number: int
    width: float
    height: float
    orientation: Orientation
    word_count: int
    text_characters: int
    image_count: int
    image_coverage: float
    vector_objects: int


def classify_page(metrics: PageMetrics) -> PageMode:
    """Choose a conservative editable, hybrid, or raster treatment."""
    if metrics.text_characters < 80 and (
        metrics.image_coverage >= 0.20 or metrics.vector_objects >= 80
    ):
        return "raster"
    if metrics.image_coverage >= 0.65:
        return "raster"
    if metrics.orientation == "landscape" and (
        metrics.image_coverage >= 0.35 or metrics.vector_objects >= 140
    ):
        return "raster"
    if (
        metrics.word_count >= 120
        and metrics.image_coverage < 0.15
        and metrics.vector_objects < 100
    ):
        return "editable"
    return "hybrid"


def _source_pdf(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() != ".pdf" or not resolved.is_file():
        raise PdfHybridError(reason=f"PDF 입력 파일이 없습니다: {resolved}")
    return resolved


def _output_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.exists() and (
        not resolved.is_dir() or any(resolved.iterdir())
    ):
        raise PdfHybridError(reason=f"출력 폴더가 비어 있지 않습니다: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _metrics(page: Page, number: int) -> tuple[PageMetrics, str]:
    text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
    words = page.extract_words(x_tolerance=2, y_tolerance=3)
    width = float(page.width)
    height = float(page.height)
    page_area = max(width * height, 1.0)
    image_area = 0.0
    images = image_boxes(page)
    for image in images:
        image_width = max(image.x1 - image.x0, 0.0)
        image_height = max(image.bottom - image.top, 0.0)
        image_area += image_width * image_height
    if width > height:
        orientation: Orientation = "landscape"
    elif height > width:
        orientation = "portrait"
    else:
        orientation = "square"
    return (
        PageMetrics(
            number=number,
            width=width,
            height=height,
            orientation=orientation,
            word_count=len(words),
            text_characters=len("".join(text.split())),
            image_count=len(images),
            image_coverage=min(image_area / page_area, 1.0),
            vector_objects=len(page.rects) + len(page.lines) + len(page.curves),
        ),
        text,
    )


def analyze_pdf(
    source_pdf: Path,
    output_dir: Path,
    *,
    dpi: int,
    pages: str | None = None,
) -> Path:
    """Analyze selected PDF pages and emit a strict manifest v2 package."""
    total_started = perf_counter()
    metadata_started = perf_counter()
    source = _source_pdf(source_pdf)
    with source.open("rb") as source_file:
        source_sha256 = hashlib.file_digest(source_file, "sha256").hexdigest()

    with pdfplumber.open(source) as document:
        source_page_count = len(document.pages)
        try:
            selected_pages = resolve_page_selection(pages, source_page_count)
        except PageSelectionError as error:
            raise PdfHybridError(reason=str(error)) from None
        render_batches = contiguous_ranges(selected_pages)
        metadata_seconds = perf_counter() - metadata_started

        renderer = find_pdftoppm()
        output = _output_dir(output_dir)
        pages_dir, text_dir, media_dir = (
            output / "pages",
            output / "text",
            output / "media",
        )
        for directory in (pages_dir, text_dir, media_dir):
            _ = directory.mkdir()

        render_started = perf_counter()
        rendered = render_pdf_batches(
            RenderPlan(
                renderer=renderer,
                source_pdf=source,
                pages_dir=pages_dir,
                dpi=dpi,
                batches=tuple(render_batches),
            )
        )
        render_seconds = perf_counter() - render_started
        rendered_by_number = {item.number: item.path for item in rendered}

        analysis_started = perf_counter()
        records: list[PageRecord] = []
        for number in selected_pages:
            page = document.pages[number - 1]
            metrics, text = _metrics(page, number)
            mode = classify_page(metrics)
            raster_path = rendered_by_number[number]
            media_files: list[str] = []
            if mode != "raster":
                media_files = _crop_media(
                    page,
                    CropTarget(
                        raster_path=raster_path,
                        media_dir=media_dir,
                        page_number=number,
                    ),
                )
            if mode == "hybrid" and not media_files:
                mode = "raster"

            text_relative: str | None = None
            if mode != "raster" and text.strip():
                text_relative = f"text/page-{number:04d}.txt"
                _ = (output / text_relative).write_text(text, encoding="utf-8")
            records.append(
                PageRecord(
                    number=number,
                    mode=mode,
                    width_pt=metrics.width,
                    height_pt=metrics.height,
                    orientation=metrics.orientation,
                    raster_file=raster_path.relative_to(output).as_posix(),
                    text_file=text_relative,
                    media_files=media_files,
                )
            )
        analysis_seconds = perf_counter() - analysis_started

    timings = ManifestTimings(
        metadata_seconds=metadata_seconds,
        render_seconds=render_seconds,
        analysis_seconds=analysis_seconds,
        total_seconds=perf_counter() - total_started,
    )
    manifest = ManifestV2(
        schema_version=2,
        source_pdf=str(source),
        source_sha256=source_sha256,
        source_page_count=source_page_count,
        selected_page_count=len(selected_pages),
        selected_pages=selected_pages,
        dpi=dpi,
        render_batches=render_batches,
        timings=timings,
        summary=ModeSummary(
            editable=sum(record.mode == "editable" for record in records),
            hybrid=sum(record.mode == "hybrid" for record in records),
            raster=sum(record.mode == "raster" for record in records),
        ),
        pages=records,
    )
    manifest_path = output / "manifest.json"
    _ = manifest_path.write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    return manifest_path


@app.callback()
def main() -> None:
    """Analyze PDFs for editable, hybrid, and raster HWP conversion."""


@app.command("analyze")
def analyze_command(
    source_pdf: Path,
    output_dir: Path,
    dpi: Annotated[int, _DPI_OPTION] = 180,
    pages: Annotated[
        str | None,
        _PAGES_OPTION,
    ] = None,
) -> None:
    """Create a hybrid conversion package from selected PDF pages."""
    manifest = analyze_pdf(source_pdf, output_dir, dpi=dpi, pages=pages)
    console.print(f"[green]완료[/green]: {manifest}")


if __name__ == "__main__":
    app()
