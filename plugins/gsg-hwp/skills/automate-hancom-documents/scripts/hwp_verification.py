from __future__ import annotations

import hashlib
from pathlib import Path
from time import perf_counter
from typing import Final

import pdfplumber
from pdfminer.pdfparser import PDFSyntaxError
from PIL import Image
from pydantic import ValidationError

from hwp_runtime import DocumentAutomationError, HwpSession, input_document
from manifest_contract import ManifestV2, PageSelectionError, contiguous_ranges, resolve_page_selection
from pdf_rendering import RenderPlan, find_pdftoppm, render_pdf_batches
from verification_contract import ContentBoundingBoxPixels, EdgeTouch, InspectionState
from verification_contract import LoadedManifest, PdfGeometry
from verification_contract import PdfInspectionRequest as PdfInspectionRequest
from verification_contract import ReportInputs, VerificationPageRecord, VerificationReport
from verification_contract import VerificationRequest as VerificationRequest
from verification_contract import build_report, geometry_matches, pdf_geometry

PIXEL_DARK_THRESHOLD: Final = 245


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _input_file(path: Path, label: str, suffix: str | None = None) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or (
        suffix is not None and resolved.suffix.casefold() != suffix
    ):
        raise DocumentAutomationError(f"{label} 파일이 없습니다: {resolved}")
    return resolved


def _review_directory(path: Path, exported_pdf: Path | None = None) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.exists() and not resolved.is_dir():
        raise DocumentAutomationError(f"검수 경로가 폴더가 아닙니다: {resolved}")
    allowed = exported_pdf.resolve() if exported_pdf is not None else None
    if resolved.exists() and any(
        allowed is None or item.resolve() != allowed for item in resolved.iterdir()
    ):
        raise DocumentAutomationError(f"검수 폴더가 비어 있지 않습니다: {resolved}")
    return resolved


def _validate_counts(dpi: int, expected_page_count: int | None) -> None:
    if dpi < 96 or dpi > 600:
        raise DocumentAutomationError("dpi는 96~600이어야 합니다")
    if expected_page_count is not None and expected_page_count <= 0:
        raise DocumentAutomationError("expected page count는 양수여야 합니다")


def _load_manifest(path: Path | None) -> LoadedManifest | None:
    if path is None:
        return None
    source = _input_file(path, "manifest")
    try:
        text = source.read_text(encoding="utf-8")
        manifest = ManifestV2.model_validate_json(text)
    except (OSError, UnicodeError, ValidationError) as error:
        raise DocumentAutomationError(f"manifest 오류 ({source}): {error}") from error
    return LoadedManifest(source, _sha256(source), manifest)


def _selected_pages(spec: str | None, page_count: int) -> tuple[int, ...]:
    try:
        return tuple(resolve_page_selection(spec, page_count))
    except PageSelectionError as error:
        raise DocumentAutomationError(str(error)) from None


def _threshold_pixel(value: int) -> int:
    return 255 if value < PIXEL_DARK_THRESHOLD else 0


def _image_metrics(path: Path) -> tuple[float, ContentBoundingBoxPixels | None, list[EdgeTouch]]:
    try:
        with Image.open(path) as image:
            grayscale = image.convert("L")
            width, height = grayscale.size
            histogram = grayscale.histogram()
            dark_pixels = sum(histogram[:PIXEL_DARK_THRESHOLD])
            mask = grayscale.point(_threshold_pixel)
            bounds = mask.getbbox()
    except OSError as error:
        raise DocumentAutomationError(f"렌더링 이미지를 읽을 수 없습니다: {path}") from error
    ratio = dark_pixels / float(width * height)
    if bounds is None:
        return ratio, None, []
    left, top, right, bottom = bounds
    edges: list[EdgeTouch] = []
    if left == 0:
        edges.append("left")
    if top == 0:
        edges.append("top")
    if right == width:
        edges.append("right")
    if bottom == height:
        edges.append("bottom")
    return ratio, ContentBoundingBoxPixels(left=left, top=top, right=right, bottom=bottom), edges


def _pdf_geometries(path: Path) -> tuple[PdfGeometry, ...]:
    try:
        with pdfplumber.open(path) as pdf:
            return tuple(pdf_geometry(float(page.width), float(page.height)) for page in pdf.pages)
    except (OSError, PDFSyntaxError) as error:
        raise DocumentAutomationError(f"출력 PDF를 읽을 수 없습니다: {path}") from error


def _inspect(state: InspectionState) -> VerificationReport:
    read_started = perf_counter()
    request = state.request
    geometries = _pdf_geometries(request.exported_pdf)
    exported_pdf_sha256 = _sha256(request.exported_pdf)
    pdf_read_seconds = perf_counter() - read_started
    if not geometries or max(state.selected_pages) > len(geometries):
        raise DocumentAutomationError("출력 PDF에 선택한 쪽이 없습니다")
    renderer = find_pdftoppm()
    _ = request.review_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = request.review_dir / "pages"
    _ = pages_dir.mkdir()
    render_started = perf_counter()
    rendered = render_pdf_batches(
        RenderPlan(
            renderer=renderer,
            source_pdf=request.exported_pdf,
            pages_dir=pages_dir,
            dpi=request.dpi,
            batches=tuple(contiguous_ranges(state.selected_pages)),
        )
    )
    render_seconds = perf_counter() - render_started
    rendered_by_number = {item.number: item.path for item in rendered}

    analysis_started = perf_counter()
    errors: list[str] = []
    warnings: list[str] = []
    if len(geometries) != request.hwp_output_page_count:
        errors.append("exported PDF page count does not match HWP output page count")
    if request.expected_page_count is not None and request.expected_page_count != request.hwp_output_page_count:
        errors.append("expected page count does not match HWP output page count")
    if state.manifest is not None and state.manifest.data.selected_page_count != request.hwp_output_page_count:
        errors.append("manifest selected page count does not match HWP output page count")

    records: list[VerificationPageRecord] = []
    for number in state.selected_pages:
        geometry = geometries[number - 1]
        ratio, bounds, edge_warnings = _image_metrics(rendered_by_number[number])
        manifest_page = None
        if state.manifest is not None and number <= len(state.manifest.data.pages):
            manifest_page = state.manifest.data.pages[number - 1]
        geometry_match = None if manifest_page is None else geometry_matches(geometry, manifest_page)
        if bounds is None:
            errors.append(f"output page {number} is blank")
        if geometry_match is False:
            errors.append(f"output page {number} geometry does not match manifest")
        warnings.extend(f"output page {number} content touches {edge} edge" for edge in edge_warnings)
        records.append(
            VerificationPageRecord(
                output_page_number=number,
                source_page_number=None if manifest_page is None else manifest_page.number,
                rendered_image=(
                    rendered_by_number[number]
                    .relative_to(request.review_dir)
                    .as_posix()
                ),
                width_pt=geometry.width_pt,
                height_pt=geometry.height_pt,
                orientation=geometry.orientation,
                nonblank_ratio=ratio,
                content_bbox_pixels=bounds,
                edge_touch_warnings=edge_warnings,
                geometry_match=geometry_match,
            )
        )
    analysis_seconds = perf_counter() - analysis_started
    report = build_report(
        ReportInputs(
            state=state,
            exported_pdf_sha256=exported_pdf_sha256,
            pdf_read_seconds=pdf_read_seconds,
            render_seconds=render_seconds,
            analysis_seconds=analysis_seconds,
            records=tuple(records),
            errors=tuple(errors),
            warnings=tuple(warnings),
        )
    )
    _ = (request.review_dir / "verification.json").write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return report


def inspect_exported_pdf(request: PdfInspectionRequest) -> VerificationReport:
    started = perf_counter()
    source = _input_file(request.source_document, "source document")
    exported = _input_file(request.exported_pdf, "exported PDF", ".pdf")
    review = _review_directory(request.review_dir, exported)
    _validate_counts(request.dpi, request.expected_page_count)
    manifest = _load_manifest(request.manifest)
    selected = _selected_pages(request.pages, request.hwp_output_page_count)
    state = InspectionState(
        request=PdfInspectionRequest(
            source,
            exported,
            review,
            request.hwp_output_page_count,
            request.pages,
            request.dpi,
            request.manifest,
            request.expected_page_count,
        ),
        manifest=manifest,
        selected_pages=selected,
        source_sha256=_sha256(source),
        validation_seconds=perf_counter() - started,
        export_seconds=0.0,
    )
    return _inspect(state)


def verify_document(request: VerificationRequest) -> VerificationReport:
    started = perf_counter()
    source = input_document(request.document)
    review = _review_directory(request.review_dir)
    _validate_counts(request.dpi, request.expected_page_count)
    manifest = _load_manifest(request.manifest)
    source_sha256 = _sha256(source)
    with HwpSession(visible=False) as hwp:
        if not hwp.open(str(source)):
            raise DocumentAutomationError(f"한컴 문서 열기 실패: {source}")
        page_count = int(hwp.PageCount)
        selected = _selected_pages(request.pages, page_count)
        validation_seconds = perf_counter() - started
        _ = review.mkdir(parents=True, exist_ok=True)
        exported = review / f"{source.stem}.pdf"
        export_started = perf_counter()
        if not hwp.save_as(str(exported), format="PDF"):
            raise DocumentAutomationError(f"PDF 출력 실패: {exported}")
        export_seconds = perf_counter() - export_started
    return _inspect(
        InspectionState(
            request=PdfInspectionRequest(
                source,
                exported,
                review,
                page_count,
                request.pages,
                request.dpi,
                request.manifest,
                request.expected_page_count,
            ),
            manifest=manifest,
            selected_pages=selected,
            source_sha256=source_sha256,
            validation_seconds=validation_seconds,
            export_seconds=export_seconds,
        )
    )
