from __future__ import annotations

from statistics import median

from PIL import Image, ImageStat


def _column_background_distance(
    image: Image.Image,
    background: tuple[float, float, float],
    x_position: int,
    rows: tuple[int, ...],
    row_breakpoints: tuple[float, ...],
) -> float:
    _, height = image.size
    distances: list[float] = []
    for row in rows:
        y0 = round(row_breakpoints[row] * height)
        y1 = round(row_breakpoints[row + 1] * height)
        inset = max(1, (y1 - y0) // 6)
        if y1 - inset <= y0 + inset:
            continue
        color = ImageStat.Stat(
            image.crop((x_position, y0 + inset, x_position + 1, y1 - inset))
        ).mean
        distances.append(
            sum(
                abs(channel - reference)
                for channel, reference in zip(
                    color,
                    background,
                    strict=True,
                )
            )
            / 765
        )
    return median(distances) if distances else 1.0


def strip_background_distance(
    image: Image.Image,
    background: tuple[float, float, float],
    left: float,
    right: float,
    rows: tuple[int, ...],
    row_breakpoints: tuple[float, ...],
) -> float:
    width, height = image.size
    x0 = round(left * width) + 1
    x1 = round(right * width) - 1
    if x1 <= x0:
        return 1.0
    distances: list[float] = []
    for row in rows:
        y0 = round(row_breakpoints[row] * height)
        y1 = round(row_breakpoints[row + 1] * height)
        inset = max(1, (y1 - y0) // 6)
        if y1 - inset <= y0 + inset:
            continue
        color = ImageStat.Stat(
            image.crop((x0, y0 + inset, x1, y1 - inset))
        ).mean
        distances.append(
            sum(
                abs(channel - reference)
                for channel, reference in zip(
                    color,
                    background,
                    strict=True,
                )
            )
            / 765
        )
    return median(distances) if distances else 1.0


def refine_blank_run(
    image: Image.Image,
    background: tuple[float, float, float],
    left: float,
    right: float,
    rows: tuple[int, ...],
    row_breakpoints: tuple[float, ...],
) -> tuple[float, float]:
    width, _ = image.size
    approximate_width = right - left
    search_left = max(0, round((left - approximate_width) * width))
    search_right = min(
        width,
        round((right + approximate_width) * width),
    )
    center = min(
        search_right - 1,
        max(search_left, round((left + right) * width / 2)),
    )
    distances = {
        x_position: _column_background_distance(
            image,
            background,
            x_position,
            rows,
            row_breakpoints,
        )
        for x_position in range(search_left, search_right)
    }
    blank = {
        x_position
        for x_position, distance in distances.items()
        if distance <= 0.06
    }
    if center not in blank:
        nearby = [
            x_position
            for x_position in blank
            if left * width < x_position < right * width
        ]
        if not nearby:
            return left, right
        center = min(
            nearby,
            key=lambda x_position: abs(x_position - center),
        )
    run_left = center
    while run_left - 1 in blank:
        run_left -= 1
    run_right = center + 1
    while run_right in blank:
        run_right += 1
    return run_left / width, run_right / width
