from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, final, override

from PIL import Image
from pdfplumber.page import Page
from pydantic import BaseModel, ConfigDict

from manifest_contract import PageRange


@final
class PdfRenderingError(RuntimeError):
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class RenderPlan:
    renderer: Path
    source_pdf: Path
    pages_dir: Path
    dpi: int
    batches: tuple[PageRange, ...]


@dataclass(frozen=True, slots=True)
class RenderedPage:
    number: int
    path: Path


@dataclass(frozen=True, slots=True)
class CropTarget:
    raster_path: Path
    media_dir: Path
    page_number: int


class PdfImageBox(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    x0: float
    x1: float
    top: float
    bottom: float


def find_pdftoppm() -> Path:
    candidate = (
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native"
        / "poppler/Library/bin/pdftoppm.exe"
    )
    if candidate.is_file():
        return candidate
    found = shutil.which("pdftoppm")
    if found is not None:
        return Path(found)
    raise PdfRenderingError(reason="pdftoppm을 찾을 수 없습니다")


def image_boxes(page: Page) -> tuple[PdfImageBox, ...]:
    return tuple(PdfImageBox.model_validate(item) for item in page.images)


def render_pdf_batches(plan: RenderPlan) -> tuple[RenderedPage, ...]:
    rendered: list[RenderedPage] = []
    for batch in plan.batches:
        prefix = plan.pages_dir / f".render-{batch.start:04d}-{batch.end:04d}"
        _ = subprocess.run(
            [
                str(plan.renderer),
                "-f",
                str(batch.start),
                "-l",
                str(batch.end),
                "-forcenum",
                "-png",
                "-r",
                str(plan.dpi),
                "-q",
                str(plan.source_pdf),
                str(prefix),
            ],
            check=True,
        )
        emitted: dict[int, Path] = {}
        for path in prefix.parent.glob(f"{prefix.name}-*.png"):
            number_text = path.stem.removeprefix(f"{prefix.name}-")
            if number_text.isdigit():
                emitted[int(number_text)] = path
        for number in range(batch.start, batch.end + 1):
            source_path = emitted.get(number)
            if source_path is None:
                raise PdfRenderingError(
                    reason=f"렌더링 결과가 없습니다: 원본 {number}쪽"
                )
            destination = plan.pages_dir / f"page-{number:04d}.png"
            _ = source_path.replace(destination)
            rendered.append(RenderedPage(number=number, path=destination))
    return tuple(rendered)


def crop_media(page: Page, target: CropTarget) -> list[str]:
    results: list[str] = []
    images = image_boxes(page)
    with Image.open(target.raster_path) as rendered:
        width_px, height_px = rendered.size
        for index, item in enumerate(images, start=1):
            x0 = item.x0
            x1 = item.x1
            top = item.top
            bottom = item.bottom
            coverage = max(x1 - x0, 0) * max(bottom - top, 0) / (
                float(page.width) * float(page.height)
            )
            if coverage < 0.02 or coverage > 0.90:
                continue
            box = (
                max(0, round(x0 / float(page.width) * width_px)),
                max(0, round(top / float(page.height) * height_px)),
                min(width_px, round(x1 / float(page.width) * width_px)),
                min(height_px, round(bottom / float(page.height) * height_px)),
            )
            if box[2] - box[0] < 10 or box[3] - box[1] < 10:
                continue
            filename = f"page-{target.page_number:04d}-image-{index:02d}.png"
            rendered.crop(box).save(target.media_dir / filename)
            results.append(f"media/{filename}")
    return results
