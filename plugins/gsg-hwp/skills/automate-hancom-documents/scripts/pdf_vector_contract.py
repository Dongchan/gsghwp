from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import ClassVar, Final, Literal, Protocol

import pymupdf
from pydantic import BaseModel, ConfigDict, TypeAdapter


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
type JsonInput = (
    JsonScalar
    | pymupdf.Point
    | pymupdf.Rect
    | pymupdf.Quad
    | Sequence["JsonInput"]
    | Mapping[str, "JsonInput"]
)
type JsonObject = dict[str, JsonValue]
type DrawingItem = tuple[JsonInput, ...]
type PlumberObject = Mapping[str, JsonInput]
type AxisOrientation = Literal["horizontal", "vertical"]

_THIN_RECT_LIMIT_PT: Final = 1.5
_ROUND_DIGITS: Final = 6
_FLOAT_ADAPTER: Final[TypeAdapter[float]] = TypeAdapter(float)
_POINT_ADAPTER: Final[TypeAdapter[tuple[float, float]]] = TypeAdapter(
    tuple[float, float]
)
_RECT_ADAPTER: Final[TypeAdapter[tuple[float, float, float, float]]] = TypeAdapter(
    tuple[float, float, float, float]
)
_MATRIX_ADAPTER: Final[TypeAdapter[tuple[float, float, float, float, float, float]]] = (
    TypeAdapter(tuple[float, float, float, float, float, float])
)
_QUAD_ADAPTER: Final[TypeAdapter[tuple[pymupdf.Point, ...]]] = TypeAdapter(
    tuple[pymupdf.Point, ...],
    config=ConfigDict(arbitrary_types_allowed=True),
)


class PlumberPage(Protocol):
    @property
    def lines(self) -> Sequence[PlumberObject]: ...

    @property
    def rects(self) -> Sequence[PlumberObject]: ...

    @property
    def curves(self) -> Sequence[PlumberObject]: ...

    @property
    def chars(self) -> Sequence[PlumberObject]: ...

    @property
    def images(self) -> Sequence[PlumberObject]: ...

    @property
    def rotation(self) -> int | None: ...

    @property
    def width(self) -> float: ...

    @property
    def height(self) -> float: ...


class MuPdfDrawing(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True,
        frozen=True,
    )

    width: float | None
    color: tuple[float, ...] | None
    fill: tuple[float, ...] | None
    dashes: str | None
    closePath: bool | None
    items: list[DrawingItem]
    type: str
    rect: pymupdf.Rect
    seqno: int


class MuPdfPage(Protocol):
    def get_drawings(
        self,
        extended: bool = False,
    ) -> Sequence[Mapping[str, JsonInput]]: ...


class MuPdfPageMetadata(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True,
        frozen=True,
        from_attributes=True,
    )

    rotation: int
    rect: pymupdf.Rect
    cropbox: pymupdf.Rect
    rotation_matrix: pymupdf.Matrix


@dataclass(frozen=True, slots=True)
class RectGeometry:
    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def is_thin(self) -> bool:
        return min(self.width, self.height) < _THIN_RECT_LIMIT_PT

    @property
    def major_length(self) -> float:
        return max(self.width, self.height)

    @property
    def thickness(self) -> float:
        return min(self.width, self.height)

    @property
    def aspect_ratio(self) -> float:
        if self.thickness <= 0:
            return float("inf")
        return self.major_length / self.thickness

    @classmethod
    def from_pymupdf(cls, value: JsonInput) -> RectGeometry:
        x0, top, x1, bottom = _RECT_ADAPTER.validate_python(value)
        return cls(x0, top, x1, bottom)

    def centerline(self, source: str) -> JsonObject:
        if self.width >= self.height:
            middle = (self.top + self.bottom) / 2
            start = (self.x0, middle)
            end = (self.x1, middle)
            orientation = "horizontal"
        else:
            middle = (self.x0 + self.x1) / 2
            start = (middle, self.top)
            end = (middle, self.bottom)
            orientation = "vertical"
        return {
            "source": source,
            "orientation": orientation,
            "start": start,
            "end": end,
            "thickness_pt": round(min(self.width, self.height), _ROUND_DIGITS),
        }

    def transformed(self, matrix: pymupdf.Matrix) -> RectGeometry:
        a, b, c, d, e, f = matrix_values(matrix)
        source_corners = (
            (self.x0, self.top),
            (self.x1, self.top),
            (self.x1, self.bottom),
            (self.x0, self.bottom),
        )
        corners = tuple(
            (x * a + y * c + e, x * b + y * d + f) for x, y in source_corners
        )
        return RectGeometry(
            x0=min(point[0] for point in corners),
            top=min(point[1] for point in corners),
            x1=max(point[0] for point in corners),
            bottom=max(point[1] for point in corners),
        )


@dataclass(frozen=True, slots=True)
class ThinRectObservation:
    geometry: RectGeometry
    source: str
    engine: Literal["pdfplumber", "pymupdf"]
    stroke: bool
    fill: bool
    linewidth_pt: float | None
    color: tuple[float, ...] | None
    paint_order: int


@dataclass(frozen=True, slots=True)
class AxisSegment:
    orientation: AxisOrientation
    coordinate: float
    start: float
    end: float
    linewidth_pt: float
    color: tuple[float, ...] | None
    source: str
    source_kind: Literal["line", "rectangle", "quad", "bezier", "thin_rect"]
    paint_order: int

    @property
    def length(self) -> float:
        return self.end - self.start

    def payload(self) -> JsonObject:
        return {
            "orientation": self.orientation,
            "coordinate_pt": round(self.coordinate, _ROUND_DIGITS),
            "start_pt": round(self.start, _ROUND_DIGITS),
            "end_pt": round(self.end, _ROUND_DIGITS),
            "length_pt": round(self.length, _ROUND_DIGITS),
            "linewidth_pt": round(self.linewidth_pt, _ROUND_DIGITS),
            "color": self.color,
            "source": self.source,
            "source_kind": self.source_kind,
            "paint_order": self.paint_order,
        }


@dataclass(frozen=True, slots=True)
class CoordinateCluster:
    coordinate: float
    minimum: float
    maximum: float
    member_count: int

    def payload(self) -> JsonObject:
        return {
            "coordinate_pt": round(self.coordinate, _ROUND_DIGITS),
            "minimum_pt": round(self.minimum, _ROUND_DIGITS),
            "maximum_pt": round(self.maximum, _ROUND_DIGITS),
            "span_pt": round(self.maximum - self.minimum, _ROUND_DIGITS),
            "member_count": self.member_count,
            "representative": "statistical_median",
        }


@dataclass(frozen=True, slots=True)
class PlumberObservation:
    payload: JsonObject
    widths: tuple[float, ...]
    line_count: int
    rect_count: int
    curve_count: int
    thin_rect_count: int
    char_count: int
    rotation: int
    size: tuple[float, float]
    thin_rectangles: tuple[ThinRectObservation, ...]


@dataclass(frozen=True, slots=True)
class MuPdfObservation:
    payload: JsonObject
    widths: tuple[float, ...]
    path_count: int
    item_count: int
    thin_rect_count: int
    bezier_count: int
    rotation: int
    size: tuple[float, float]
    unrotated_size: tuple[float, float]
    rotation_matrix: tuple[float, float, float, float, float, float]
    derotation_matrix: pymupdf.Matrix
    drawings: tuple[MuPdfDrawing, ...]
    thin_rectangles: tuple[ThinRectObservation, ...]


@dataclass(frozen=True, slots=True)
class ExtractionReport:
    source_pdf: str
    page_number: int
    page: JsonObject
    pdfplumber: JsonObject
    pymupdf: JsonObject
    summary: JsonObject
    comparison: JsonObject
    grid_reconstruction: JsonObject
    unit_alignment: JsonObject


def json_safe(value: JsonInput) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, pymupdf.Point):
        return _POINT_ADAPTER.validate_python(value)
    if isinstance(value, pymupdf.Rect):
        return _RECT_ADAPTER.validate_python(value)
    if isinstance(value, pymupdf.Quad):
        return tuple(
            _POINT_ADAPTER.validate_python(point)
            for point in _QUAD_ADAPTER.validate_python(value)
        )
    if isinstance(value, Mapping):
        return {key: json_safe(item) for key, item in value.items()}
    return tuple(json_safe(item) for item in value)


def drawing_json(drawing: MuPdfDrawing) -> JsonObject:
    return {
        "width": drawing.width,
        "color": drawing.color,
        "fill": drawing.fill,
        "dashes": drawing.dashes,
        "closePath": drawing.closePath,
        "items": tuple(json_safe(item) for item in drawing.items),
        "type": drawing.type,
        "rect": json_safe(drawing.rect),
        "seqno": drawing.seqno,
    }


def parse_mupdf_drawing(value: Mapping[str, JsonInput]) -> MuPdfDrawing:
    return MuPdfDrawing.model_validate(value)


def parse_mupdf_page_metadata(page: pymupdf.Page) -> MuPdfPageMetadata:
    return MuPdfPageMetadata.model_validate(page)


def as_mupdf_page(page: pymupdf.Page) -> MuPdfPage:
    return page


def json_float(value: JsonInput) -> float:
    return _FLOAT_ADAPTER.validate_python(value)


def pt_to_hwpunit(value: float) -> int:
    points = Decimal(str(value))
    if not points.is_finite():
        raise ValueError("point value must be finite")
    return int((points * 100).to_integral_value(rounding=ROUND_HALF_UP))


def point_values(value: JsonInput) -> tuple[float, float]:
    return _POINT_ADAPTER.validate_python(value)


def quad_values(value: pymupdf.Quad) -> tuple[pymupdf.Point, ...]:
    upper_left, upper_right, lower_left, lower_right = _QUAD_ADAPTER.validate_python(
        value
    )
    return upper_left, upper_right, lower_right, lower_left


def rect_size(rect: pymupdf.Rect) -> tuple[float, float]:
    x0, top, x1, bottom = _RECT_ADAPTER.validate_python(rect)
    return x1 - x0, bottom - top


def matrix_values(
    matrix: pymupdf.Matrix,
) -> tuple[float, float, float, float, float, float]:
    return _MATRIX_ADAPTER.validate_python(matrix)


def selected_fields(
    values: Mapping[str, JsonInput],
    fields: tuple[str, ...],
) -> JsonObject:
    return {field: json_safe(values.get(field)) for field in fields}


def frequency(values: Sequence[float]) -> tuple[JsonObject, ...]:
    counts = Counter(round(value, _ROUND_DIGITS) for value in values)
    return tuple(
        {"value_pt": value, "count": count} for value, count in sorted(counts.items())
    )


def rect_gaps(rectangles: Sequence[RectGeometry]) -> JsonObject:
    horizontal: list[float] = []
    vertical: list[float] = []
    for first in rectangles:
        horizontal_candidates = tuple(
            second.x0 - first.x1
            for second in rectangles
            if second.x0 >= first.x1
            and min(first.bottom, second.bottom) > max(first.top, second.top)
        )
        vertical_candidates = tuple(
            second.top - first.bottom
            for second in rectangles
            if second.top >= first.bottom
            and min(first.x1, second.x1) > max(first.x0, second.x0)
        )
        if horizontal_candidates:
            horizontal.append(min(horizontal_candidates))
        if vertical_candidates:
            vertical.append(min(vertical_candidates))
    horizontal_values = tuple(
        sorted(round(value, _ROUND_DIGITS) for value in horizontal)
    )
    vertical_values = tuple(sorted(round(value, _ROUND_DIGITS) for value in vertical))
    return {
        "horizontal": horizontal_values,
        "vertical": vertical_values,
        "horizontal_frequency": frequency(horizontal_values),
        "vertical_frequency": frequency(vertical_values),
    }
