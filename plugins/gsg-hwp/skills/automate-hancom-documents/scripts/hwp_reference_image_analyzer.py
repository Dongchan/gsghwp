from __future__ import annotations

import hashlib
import math
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
from hwp_reference_image_cache import (
    DEFAULT_REFERENCE_IMAGE_LIMITS,
    ReferenceImageResourceLimits,
    acquire_reference_image_lock,
    directory_size,
    prune_reference_image_cache,
    remove_reference_cache_directory,
)
from hwp_reference_image_contract import ReferenceImageAnalysis
from hwp_reference_image_gaps import detect_protected_gaps
from hwp_reference_image_objects import detect_objects
from hwp_reference_image_pixels import PixelCanvas
from hwp_reference_image_segment_gaps import exclude_gap_crossing_segments
from hwp_reference_image_segments import detect_visible_segments
from hwp_reference_image_text import detect_text_regions


ANALYZER_VERSION = "1.1.0"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_artifact_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    return base / "GSG_HWP_BETA" / "reference-image-analysis"


def _artifact_paths(analysis: ReferenceImageAnalysis) -> tuple[Path, ...]:
    return (
        analysis.overlay_path,
        *analysis.contact_sheet_paths,
        *(tile.path for tile in analysis.tiles),
        *(region.crop_path for region in analysis.text_regions),
    )


def _has_complete_artifacts(
    analysis: ReferenceImageAnalysis,
    directory: Path,
) -> bool:
    resolved_directory = directory.resolve()
    for path in _artifact_paths(analysis):
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(resolved_directory) or not resolved.is_file():
                return False
        except OSError:
            return False
    return True


def _load_complete_analysis(
    result_path: Path,
    *,
    source: Path,
    image_hash: str,
    analysis_id: str,
    resource_fingerprint: str | None,
) -> ReferenceImageAnalysis | None:
    try:
        cached = ReferenceImageAnalysis.model_validate_json(
            result_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if (
        cached.analysis_id != analysis_id
        or cached.analyzer_version != ANALYZER_VERSION
        or cached.image_hash != image_hash
        or cached.source_image.resolve() != source
        or (
            resource_fingerprint is not None
            and cached.resource_fingerprint != resource_fingerprint
        )
        or not _has_complete_artifacts(cached, result_path.parent)
    ):
        return None
    try:
        result_path.touch()
    except OSError:
        return None
    return cached.model_copy(update={"cache_hit": True})


def _source_metadata(
    source: Path,
    limits: ReferenceImageResourceLimits,
) -> tuple[int, int, int]:
    source_bytes = source.stat().st_size
    if source_bytes > limits.max_source_bytes:
        raise ValueError("reference image source byte limit exceeded")
    try:
        with Image.open(source) as image:
            width, height = image.size
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"reference image cannot be decoded: {source}") from error
    if width < 2 or height < 2:
        raise ValueError("reference image dimensions must be at least 2 by 2")
    if width * height > limits.max_source_pixels:
        raise ValueError("reference image source pixel limit exceeded")
    return source_bytes, width, height


def _analysis_size(
    width: int,
    height: int,
    max_pixels: int,
) -> tuple[int, int]:
    pixels = width * height
    if pixels <= max_pixels:
        return width, height
    scale = math.sqrt(max_pixels / pixels)
    target_width = max(2, math.floor(width * scale))
    target_height = max(2, math.floor(height * scale))
    while target_width * target_height > max_pixels:
        if target_width >= target_height:
            target_width -= 1
        else:
            target_height -= 1
    return target_width, target_height


def _load_canvas(
    source: Path,
    target_size: tuple[int, int],
) -> PixelCanvas:
    try:
        with Image.open(source) as image:
            if image.size != target_size:
                _ = image.draft("RGB", target_size)
                image.thumbnail(
                    target_size,
                    resample=Image.Resampling.LANCZOS,
                    reducing_gap=3,
                )
            return PixelCanvas.from_image(image)
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"reference image cannot be decoded: {source}") from error


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
    cached = _load_complete_analysis(
        result_path,
        source=source,
        image_hash=_file_hash(source),
        analysis_id=analysis_id,
        resource_fingerprint=None,
    )
    if cached is None:
        raise ValueError("reference image analysis cache is stale or incomplete")
    return cached


def _final_path(path: Path, temporary: Path, directory: Path) -> Path:
    return directory / path.relative_to(temporary)


def _analyze_into_directory(
    source: Path,
    *,
    source_bytes: int,
    source_width: int,
    source_height: int,
    image_hash: str,
    analysis_id: str,
    resource_fingerprint: str,
    temporary: Path,
    directory: Path,
    limits: ReferenceImageResourceLimits,
    started: float,
) -> ReferenceImageAnalysis:
    target_size = _analysis_size(
        source_width,
        source_height,
        limits.max_analysis_pixels,
    )
    crops_directory = temporary / "text-crops"
    crops_directory.mkdir(parents=True)
    canvas = _load_canvas(source, target_size)
    try:
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
        temporary_texts = compile_texts(
            canvas,
            ANALYZER_VERSION,
            image_hash,
            pixel_texts,
            directory=crops_directory,
        )
        temporary_tiles = save_tiles(canvas, directory=temporary)
        temporary_contact_sheets = save_contact_sheets(
            temporary_tiles,
            directory=temporary,
        )
        temporary_overlay = temporary / "evidence-overlay.png"
        save_overlay(
            canvas,
            objects=pixel_objects,
            text_regions=pixel_texts,
            gaps=pixel_gaps,
            segments=segments,
            path=temporary_overlay,
        )
        texts = tuple(
            item.model_copy(
                update={
                    "crop_path": _final_path(
                        item.crop_path,
                        temporary,
                        directory,
                    )
                }
            )
            for item in temporary_texts
        )
        tiles = tuple(
            item.model_copy(
                update={"path": _final_path(item.path, temporary, directory)}
            )
            for item in temporary_tiles
        )
        contact_sheets = tuple(
            _final_path(path, temporary, directory)
            for path in temporary_contact_sheets
        )
        result = ReferenceImageAnalysis(
            analysis_id=analysis_id,
            analyzer_version=ANALYZER_VERSION,
            image_hash=image_hash,
            source_image=source,
            image_width=source_width,
            image_height=source_height,
            source_bytes=source_bytes,
            analysis_width=canvas.width,
            analysis_height=canvas.height,
            analysis_downsampled=(canvas.width, canvas.height)
            != (source_width, source_height),
            resource_fingerprint=resource_fingerprint,
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
            overlay_path=_final_path(
                temporary_overlay,
                temporary,
                directory,
            ),
        )
        temporary_result = temporary / "result.json.tmp"
        _ = temporary_result.write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )
        _ = temporary_result.replace(temporary / "result.json")
        if directory_size(temporary) > limits.max_analysis_artifact_bytes:
            raise ValueError("reference image analysis artifact limit exceeded")
        return result
    finally:
        canvas.close()


def analyze_reference_image(
    source_image: Path,
    *,
    artifact_root: Path | None = None,
    limits: ReferenceImageResourceLimits = DEFAULT_REFERENCE_IMAGE_LIMITS,
) -> ReferenceImageAnalysis:
    started = time.perf_counter()
    source = source_image.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"reference image does not exist: {source}")
    source_bytes, source_width, source_height = _source_metadata(source, limits)
    image_hash = _file_hash(source)
    resource_fingerprint = limits.analysis_fingerprint
    analysis_key = hashlib.sha256(
        (
            f"{ANALYZER_VERSION}|{image_hash}|{resource_fingerprint}"
        ).encode("ascii")
    ).hexdigest()
    analysis_id = f"ria-{analysis_key[:16]}"
    root = (artifact_root or default_artifact_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    prune_reference_image_cache(root, limits)
    directory = root / analysis_id
    result_path = directory / "result.json"
    lock = acquire_reference_image_lock(root, analysis_id, limits)
    temporary: Path | None = None
    try:
        cached = _load_complete_analysis(
            result_path,
            source=source,
            image_hash=image_hash,
            analysis_id=analysis_id,
            resource_fingerprint=resource_fingerprint,
        )
        if cached is not None:
            result = cached
        else:
            if directory.exists():
                remove_reference_cache_directory(root, directory)
            temporary = Path(
                tempfile.mkdtemp(
                    prefix=f".partial-{analysis_id}-",
                    dir=root,
                )
            ).resolve()
            result = _analyze_into_directory(
                source,
                source_bytes=source_bytes,
                source_width=source_width,
                source_height=source_height,
                image_hash=image_hash,
                analysis_id=analysis_id,
                resource_fingerprint=resource_fingerprint,
                temporary=temporary,
                directory=directory,
                limits=limits,
                started=started,
            )
            _ = temporary.replace(directory)
            temporary = None
    finally:
        if temporary is not None and temporary.exists():
            remove_reference_cache_directory(root, temporary)
        lock.close()
    prune_reference_image_cache(
        root,
        limits,
        protected_analysis_ids=frozenset((analysis_id,)),
    )
    return result
