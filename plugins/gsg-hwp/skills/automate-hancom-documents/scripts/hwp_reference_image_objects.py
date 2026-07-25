from __future__ import annotations

from hwp_reference_image_fills import detect_filled_regions
from hwp_reference_image_pixels import PixelBox, PixelCanvas, PixelObject, PixelSegment


def _intersection_area(left: PixelBox, right: PixelBox) -> int:
    width = max(0, min(left.right, right.right) - max(left.left, right.left))
    height = max(0, min(left.bottom, right.bottom) - max(left.top, right.top))
    return width * height


def _iou(left: PixelBox, right: PixelBox) -> float:
    intersection = _intersection_area(left, right)
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / max(1, union)


def _segment_matches_edge(
    segment: PixelSegment,
    bbox: PixelBox,
    edge: str,
) -> bool:
    tolerance = 3
    if edge in {"top", "bottom"}:
        expected = bbox.top if edge == "top" else bbox.bottom - 1
        return (
            segment.orientation == "horizontal"
            and abs(segment.position - expected) <= tolerance
            and segment.start <= bbox.left + tolerance
            and segment.end >= bbox.right - tolerance
        )
    expected = bbox.left if edge == "left" else bbox.right - 1
    return (
        segment.orientation == "vertical"
        and abs(segment.position - expected) <= tolerance
        and segment.start <= bbox.top + tolerance
        and segment.end >= bbox.bottom - tolerance
    )


def _attach_edges(
    objects: list[PixelObject],
    segments: tuple[PixelSegment, ...],
) -> list[PixelObject]:
    edges = ("top", "right", "bottom", "left")
    return [
        PixelObject(
            bbox=item.bbox,
            fill=item.fill,
            edges=tuple(
                edge
                for edge in edges
                if any(_segment_matches_edge(segment, item.bbox, edge) for segment in segments)
            ),
        )
        for item in objects
    ]


def _outlined_boxes(segments: tuple[PixelSegment, ...]) -> list[PixelObject]:
    horizontal = [item for item in segments if item.orientation == "horizontal"]
    vertical = [item for item in segments if item.orientation == "vertical"]
    boxes: list[PixelObject] = []
    for top_index, top in enumerate(horizontal):
        for bottom in horizontal[top_index + 1 :]:
            if bottom.position - top.position < 4:
                continue
            if abs(top.start - bottom.start) > 3 or abs(top.end - bottom.end) > 3:
                continue
            bbox = PixelBox(top.start, top.position, top.end, bottom.position + 1)
            left = any(_segment_matches_edge(item, bbox, "left") for item in vertical)
            right = any(_segment_matches_edge(item, bbox, "right") for item in vertical)
            if left and right:
                boxes.append(
                    PixelObject(
                        bbox=bbox,
                        fill=None,
                        edges=("top", "right", "bottom", "left"),
                    )
                )
    return boxes


def detect_objects(
    canvas: PixelCanvas,
    segments: tuple[PixelSegment, ...],
) -> tuple[PixelObject, ...]:
    filled = _attach_edges(detect_filled_regions(canvas), segments)
    for outlined in _outlined_boxes(segments):
        if not any(_iou(outlined.bbox, item.bbox) >= 0.82 for item in filled):
            filled.append(outlined)
    return tuple(filled)
