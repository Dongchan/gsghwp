from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from hwp_pageplan_g03_contract import G03GuideMetadata, G03GuideMaskReceipt


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strip_bounds(edge: int, thickness: int, limit: int) -> tuple[int, int]:
    before = thickness // 2
    start = max(0, edge - before)
    end = min(limit, start + thickness)
    return start, end


def mask_reference_guides(
    source: Path,
    output: Path,
    guide: G03GuideMetadata,
) -> G03GuideMaskReceipt:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if source == output:
        raise ValueError("guide mask output must differ from the clean source")
    if not source.is_file():
        raise ValueError(f"reference image does not exist: {source}")
    source_hash = _sha256(source)
    try:
        with Image.open(source) as opened:
            _ = opened.load()
            if opened.size != (guide.image_width_px, guide.image_height_px):
                raise ValueError("guide metadata image dimensions do not match source")
            image = opened.convert("RGB")
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"reference image cannot be decoded: {source}") from error

    width, height = image.size
    left_start, left_end = _strip_bounds(guide.left_px, guide.thickness_px, width)
    right_start, right_end = _strip_bounds(guide.right_px, guide.thickness_px, width)
    top_start, top_end = _strip_bounds(guide.top_px, guide.thickness_px, height)
    bottom_start, bottom_end = _strip_bounds(
        guide.bottom_px, guide.thickness_px, height
    )
    pixels = image.load()
    if pixels is None:
        image.close()
        raise ValueError("could not access guide-mask pixels")
    masked = 0
    for y in range(height):
        for x in range(width):
            in_vertical_edge = (
                (left_start <= x < left_end or right_start <= x < right_end)
                and guide.top_px - guide.thickness_px
                <= y
                <= guide.bottom_px + guide.thickness_px
            )
            in_horizontal_edge = (
                (top_start <= y < top_end or bottom_start <= y < bottom_end)
                and guide.left_px - guide.thickness_px
                <= x
                <= guide.right_px + guide.thickness_px
            )
            if in_vertical_edge or in_horizontal_edge:
                if pixels[x, y] != (255, 255, 255):
                    masked += 1
                pixels[x, y] = (255, 255, 255)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        image.save(temporary, format="PNG", optimize=False)
        _ = temporary.replace(output)
    except (OSError, ValueError) as error:
        temporary.unlink(missing_ok=True)
        raise ValueError(
            f"could not create guide-masked reference image: {output}"
        ) from error
    finally:
        image.close()

    if _sha256(source) != source_hash:
        raise ValueError("clean reference image changed during guide masking")
    return G03GuideMaskReceipt(
        page=guide.page,
        source_image_sha256=source_hash,
        masked_image_sha256=_sha256(output),
        image_width_px=width,
        image_height_px=height,
        masked_pixel_count=masked,
        edge_coordinates_px=(
            guide.left_px,
            guide.top_px,
            guide.right_px,
            guide.bottom_px,
        ),
        masked_image_path=output,
    )


__all__ = ["mask_reference_guides"]
