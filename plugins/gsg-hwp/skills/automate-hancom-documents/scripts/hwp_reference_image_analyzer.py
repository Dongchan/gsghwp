from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from hwp_reference_image_artifacts import (
    save_contact_sheets,
    save_overlay,
    save_tiles,
)
from hwp_reference_image_compile import (
    compile_breakpoints,
    compile_gaps,
    compile_objects,
    compile_segments,
    compile_texts,
)
from hwp_reference_image_contract import ReferenceImageAnalysis
from hwp_reference_image_gaps import detect_protected_gaps
from hwp_reference_image_objects import detect_objects
from hwp_reference_image_pixels import PixelCanvas
from hwp_reference_image_segment_gaps import exclude_gap_crossing_segments
from hwp_reference_image_segments import detect_visible_segments
from hwp_reference_image_text import detect_text_regions


ANALYZER_VERSION = "1.0.13"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_artifact_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    return base / "GSG_HWP_BETA" / "reference-image-analysis"


def load_cached_reference_image_analysis(
    source_image: Path,
    analysis_id: str,
    *,
    artifact_root: Path | None = None,
) -> ReferenceImageAnalysis:
    source = source_image.expanduser().resolve()
    result_path = (
        (artifact_root or default_artifact_root()).resolve()
        / analysis_id
        / "result.json"
    )
    if not result_path.is_file():
        message = "; ".join(
            (
                "reference image analysis is not cached",
                "call hwp_analyze_reference_image before mutation",
            )
        )
        raise ValueError(message)
    cached = ReferenceImageAnalysis.model_validate_json(
        result_path.read_text(encoding="utf-8")
    )
    if cached.analysis_id != analysis_id or cached.analyzer_version != ANALYZER_VERSION:
        raise ValueError("reference image analysis cache identity is stale")
    if cached.source_image.resolve() != source or cached.image_hash != _file_hash(
        source
    ):
        raise ValueError("reference image changed after analysis")
    return cached.model_copy(update={"cache_hit": True})


def analyze_reference_image(
    source_image: Path,
    *,
    artifact_root: Path | None = None,
) -> ReferenceImageAnalysis:
    started = time.perf_counter()
    source = source_image.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"reference image does not exist: {source}")
    image_hash = _file_hash(source)
    analysis_key = hashlib.sha256(
        f"{ANALYZER_VERSION}|{image_hash}".encode("ascii")
    ).hexdigest()
    analysis_id = f"ria-{analysis_key[:16]}"
    directory = (artifact_root or default_artifact_root()).resolve() / analysis_id
    result_path = directory / "result.json"
    if result_path.is_file():
        cached = ReferenceImageAnalysis.model_validate_json(
            result_path.read_text(encoding="utf-8")
        )
        return cached.model_copy(update={"cache_hit": True})
    _ = directory.mkdir(parents=True, exist_ok=True)
    crops_directory = directory / "text-crops"
    _ = crops_directory.mkdir(exist_ok=True)
    try:
        with Image.open(source) as image:
            _ = image.load()
            canvas = PixelCanvas.from_image(image)
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"reference image cannot be decoded: {source}") from error
    segments = detect_visible_segments(canvas)
    pixel_objects = detect_objects(canvas, segments)
    pixel_gaps = detect_protected_gaps(canvas, pixel_objects)
    segments = exclude_gap_crossing_segments(segments, pixel_gaps)
    pixel_texts = detect_text_regions(canvas, segments)
    objects = compile_objects(canvas, ANALYZER_VERSION, image_hash, pixel_objects)
    segment_contracts = compile_segments(
        canvas,
        ANALYZER_VERSION,
        image_hash,
        segments,
    )
    gaps = compile_gaps(
        canvas,
        ANALYZER_VERSION,
        image_hash,
        pixel_gaps,
        objects,
    )
    texts = compile_texts(
        canvas,
        ANALYZER_VERSION,
        image_hash,
        pixel_texts,
        directory=crops_directory,
    )
    tiles = save_tiles(canvas, directory=directory)
    contact_sheets = save_contact_sheets(tiles, directory=directory)
    overlay_path = directory / "evidence-overlay.png"
    save_overlay(
        canvas,
        objects=pixel_objects,
        text_regions=pixel_texts,
        gaps=pixel_gaps,
        segments=segments,
        path=overlay_path,
    )
    result = ReferenceImageAnalysis(
        analysis_id=analysis_id,
        analyzer_version=ANALYZER_VERSION,
        image_hash=image_hash,
        source_image=source,
        image_width=canvas.width,
        image_height=canvas.height,
        analysis_time_ms=(time.perf_counter() - started) * 1000,
        cache_hit=False,
        objects=objects,
        text_regions=texts,
        protected_gaps=gaps,
        visible_segments=segment_contracts,
        breakpoint_candidates=compile_breakpoints(
            analysis_id,
            objects,
            gaps,
            segment_contracts,
        ),
        tiles=tiles,
        contact_sheet_paths=contact_sheets,
        overlay_path=overlay_path,
    )
    temporary = directory / "result.json.tmp"
    _ = temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    _ = temporary.replace(result_path)
    return result
