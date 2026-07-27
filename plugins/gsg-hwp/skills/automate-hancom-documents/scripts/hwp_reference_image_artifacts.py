from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from hwp_reference_image_contract import NormalizedBox, ReferenceImageTile
from hwp_reference_image_pixels import PixelBox, PixelCanvas, PixelGap, PixelObject, PixelSegment


def _normalized(bbox: PixelBox, canvas: PixelCanvas) -> NormalizedBox:
    return NormalizedBox(
        left=bbox.left / canvas.width,
        top=bbox.top / canvas.height,
        right=bbox.right / canvas.width,
        bottom=bbox.bottom / canvas.height,
    )


def _axis_starts(length: int, tile_size: int, overlap: int) -> tuple[int, ...]:
    if length <= tile_size:
        return (0,)
    starts = list(range(0, length - tile_size + 1, tile_size - overlap))
    final_start = length - tile_size
    if starts[-1] != final_start:
        starts.append(final_start)
    return tuple(starts)


def save_tiles(
    canvas: PixelCanvas,
    *,
    directory: Path,
) -> tuple[ReferenceImageTile, ...]:
    tile_width = min(canvas.width, 512)
    tile_height = min(canvas.height, 512)
    overlap = min(96, max(32, min(tile_width, tile_height) // 6))
    x_starts = _axis_starts(canvas.width, tile_width, overlap)
    y_starts = _axis_starts(canvas.height, tile_height, overlap)
    tiles: list[ReferenceImageTile] = []
    for index, (top, left) in enumerate(
        (position for position in ((y, x) for y in y_starts for x in x_starts)),
        start=1,
    ):
        bbox = PixelBox(left, top, left + tile_width, top + tile_height)
        tile_path = directory / f"tile-{index:04d}.png"
        with canvas.image.crop(
            (bbox.left, bbox.top, bbox.right, bbox.bottom)
        ) as image:
            image.save(tile_path, format="PNG", optimize=False)
            image_hash = hashlib.sha256(image.tobytes()).hexdigest()
        tiles.append(
            ReferenceImageTile(
                tile_id=f"tile-{index:04d}",
                bbox=_normalized(bbox, canvas),
                pixel_width=bbox.width,
                pixel_height=bbox.height,
                image_hash=image_hash,
                path=tile_path,
            )
        )
    return tuple(tiles)


def save_contact_sheets(
    tiles: tuple[ReferenceImageTile, ...],
    *,
    directory: Path,
) -> tuple[Path, ...]:
    if not tiles:
        return ()
    label_height = 28
    columns = 2
    sheets: list[Path] = []
    font = ImageFont.load_default()
    for sheet_index, offset in enumerate(range(0, len(tiles), 4), start=1):
        group = tiles[offset : offset + 4]
        cell_width = max(item.pixel_width for item in group)
        cell_height = max(item.pixel_height for item in group) + label_height
        rows = (len(group) + columns - 1) // columns
        with Image.new(
            "RGB",
            (cell_width * columns, cell_height * rows),
            "white",
        ) as sheet:
            draw = ImageDraw.Draw(sheet)
            for index, tile in enumerate(group):
                with Image.open(tile.path) as opened:
                    with opened.convert("RGB") as image:
                        column = index % columns
                        row = index // columns
                        x_position = column * cell_width
                        y_position = row * cell_height
                        sheet.paste(image, (x_position, y_position + label_height))
                label = (
                    f"{tile.tile_id} "
                    f"[{tile.bbox.left:.4f},{tile.bbox.top:.4f},"
                    f"{tile.bbox.right:.4f},{tile.bbox.bottom:.4f}]"
                )
                draw.text(
                    (x_position + 4, y_position + 7),
                    label,
                    fill="black",
                    font=font,
                )
            path = directory / f"contact-sheet-{sheet_index:02d}.png"
            sheet.save(path, format="PNG", optimize=False)
        sheets.append(path)
    return tuple(sheets)


def save_text_crop(
    canvas: PixelCanvas,
    *,
    bbox: PixelBox,
    path: Path,
) -> str:
    with canvas.image.crop(
        (bbox.left, bbox.top, bbox.right, bbox.bottom)
    ) as crop:
        scale = 3 if crop.height < 32 else 2
        with crop.resize(
            (crop.width * scale, crop.height * scale),
            resample=Image.Resampling.NEAREST,
        ) as enlarged:
            enlarged.save(path, format="PNG", optimize=False)
        return hashlib.sha256(crop.tobytes()).hexdigest()


def save_overlay(
    canvas: PixelCanvas,
    *,
    objects: tuple[PixelObject, ...],
    text_regions: tuple[PixelBox, ...],
    gaps: tuple[PixelGap, ...],
    segments: tuple[PixelSegment, ...],
    path: Path,
) -> None:
    overlay = canvas.image.convert("RGBA")
    try:
        with Image.new("RGBA", overlay.size, (0, 0, 0, 0)) as tint:
            tint_draw = ImageDraw.Draw(tint)
            for gap in gaps:
                tint_draw.rectangle(
                    (gap.bbox.left, gap.bbox.top, gap.bbox.right, gap.bbox.bottom),
                    fill=(0, 210, 80, 70),
                    outline=(0, 150, 55, 230),
                    width=2,
                )
            composed = Image.alpha_composite(overlay, tint)
        overlay.close()
        overlay = composed
        draw = ImageDraw.Draw(overlay)
        for item in objects:
            draw.rectangle(
                (item.bbox.left, item.bbox.top, item.bbox.right, item.bbox.bottom),
                outline=(0, 100, 255, 230),
                width=2,
            )
        for bbox in text_regions:
            draw.rectangle(
                (bbox.left, bbox.top, bbox.right, bbox.bottom),
                outline=(225, 0, 225, 210),
                width=1,
            )
        for segment in segments:
            if segment.orientation == "horizontal":
                points = (
                    segment.start,
                    segment.position,
                    segment.end,
                    segment.position,
                )
            else:
                points = (
                    segment.position,
                    segment.start,
                    segment.position,
                    segment.end,
                )
            draw.line(points, fill=(255, 25, 25, 255), width=max(2, segment.width))
        with overlay.convert("RGB") as flattened:
            flattened.save(path, format="PNG", optimize=False)
    finally:
        overlay.close()
