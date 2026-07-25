from __future__ import annotations

from hwp_reference_image_pixels import PixelBox, PixelCanvas, PixelGap, PixelObject


def _overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    return max(0, min(end_a, end_b) - max(start_a, start_b))


def _contains(outer: PixelBox, inner: PixelBox) -> bool:
    return (
        outer.left <= inner.left
        and outer.top <= inner.top
        and outer.right >= inner.right
        and outer.bottom >= inner.bottom
    )


def _structural_object(canvas: PixelCanvas, bbox: PixelBox) -> bool:
    return (
        bbox.width >= max(10, canvas.width // 100)
        and bbox.height >= max(8, canvas.height // 120)
    )


def _blocked(
    corridor: PixelBox,
    objects: tuple[PixelObject, ...],
    left_index: int,
    right_index: int,
) -> bool:
    for index, item in enumerate(objects):
        if index in {left_index, right_index}:
            continue
        if _contains(item.bbox, corridor):
            continue
        overlap_width = _overlap(
            corridor.left,
            corridor.right,
            item.bbox.left,
            item.bbox.right,
        )
        overlap_height = _overlap(
            corridor.top,
            corridor.bottom,
            item.bbox.top,
            item.bbox.bottom,
        )
        if overlap_width * overlap_height >= corridor.width * corridor.height * 0.08:
            return True
    return False


def _vertical_candidate(
    canvas: PixelCanvas,
    objects: tuple[PixelObject, ...],
    left_index: int,
    right_index: int,
) -> PixelGap | None:
    left = objects[left_index].bbox
    right = objects[right_index].bbox
    if not (
        _structural_object(canvas, left)
        and _structural_object(canvas, right)
    ):
        return None
    size = right.left - left.right
    if size < 2 or size > canvas.width // 4:
        return None
    shared_top = max(left.top, right.top)
    shared_bottom = min(left.bottom, right.bottom)
    shared_height = shared_bottom - shared_top
    if shared_height < min(left.height, right.height) * 0.55:
        return None
    if max(left.height, right.height) > min(left.height, right.height) * 3:
        return None
    corridor = PixelBox(left.right, shared_top, right.left, shared_bottom)
    if _blocked(corridor, objects, left_index, right_index):
        return None
    inset = min(4, max(1, size // 4))
    interior = PixelBox(
        corridor.left + inset,
        corridor.top,
        corridor.right - inset,
        corridor.bottom,
    )
    if canvas.contrast_ratio(interior) > 0.025:
        return None
    return PixelGap(
        orientation="vertical",
        bbox=corridor,
        minimum_size=size,
        bounded_by=(left_index, right_index),
    )


def _horizontal_candidate(
    canvas: PixelCanvas,
    objects: tuple[PixelObject, ...],
    top_index: int,
    bottom_index: int,
) -> PixelGap | None:
    top = objects[top_index].bbox
    bottom = objects[bottom_index].bbox
    if not (
        _structural_object(canvas, top)
        and _structural_object(canvas, bottom)
    ):
        return None
    size = bottom.top - top.bottom
    if size < 2 or size > canvas.height // 4:
        return None
    shared_left = max(top.left, bottom.left)
    shared_right = min(top.right, bottom.right)
    shared_width = shared_right - shared_left
    if shared_width < min(top.width, bottom.width) * 0.55:
        return None
    if max(top.width, bottom.width) > min(top.width, bottom.width) * 3:
        return None
    corridor = PixelBox(shared_left, top.bottom, shared_right, bottom.top)
    if _blocked(corridor, objects, top_index, bottom_index):
        return None
    inset = min(4, max(1, size // 4))
    interior = PixelBox(
        corridor.left,
        corridor.top + inset,
        corridor.right,
        corridor.bottom - inset,
    )
    if canvas.contrast_ratio(interior) > 0.025:
        return None
    return PixelGap(
        orientation="horizontal",
        bbox=corridor,
        minimum_size=size,
        bounded_by=(top_index, bottom_index),
    )


def _same_gap(left: PixelGap, right: PixelGap) -> bool:
    if left.orientation != right.orientation:
        return False
    overlap_width = _overlap(
        left.bbox.left,
        left.bbox.right,
        right.bbox.left,
        right.bbox.right,
    )
    overlap_height = _overlap(
        left.bbox.top,
        left.bbox.bottom,
        right.bbox.top,
        right.bbox.bottom,
    )
    overlap_area = overlap_width * overlap_height
    minimum_area = min(
        left.bbox.width * left.bbox.height,
        right.bbox.width * right.bbox.height,
    )
    return overlap_area >= minimum_area * 0.7


def detect_protected_gaps(
    canvas: PixelCanvas,
    objects: tuple[PixelObject, ...],
) -> tuple[PixelGap, ...]:
    candidates: list[PixelGap] = []
    for left_index in range(len(objects)):
        for right_index in range(len(objects)):
            if left_index == right_index:
                continue
            vertical = _vertical_candidate(
                canvas,
                objects,
                left_index,
                right_index,
            )
            if vertical is not None:
                candidates.append(vertical)
            horizontal = _horizontal_candidate(
                canvas,
                objects,
                left_index,
                right_index,
            )
            if horizontal is not None:
                candidates.append(horizontal)
    retained: list[PixelGap] = []
    for candidate in sorted(
        candidates,
        key=lambda item: item.bbox.width * item.bbox.height,
        reverse=True,
    ):
        if not any(_same_gap(candidate, existing) for existing in retained):
            retained.append(candidate)
    return tuple(retained)
