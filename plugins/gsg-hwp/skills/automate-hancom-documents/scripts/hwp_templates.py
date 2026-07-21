from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from hwp_picture_placement import enforce_picture_size
from hwp_runtime import DocumentAutomationError, HwpApplication, HwpSession, input_document, output_document

ImageMode: TypeAlias = Literal["cell-fill", "inline"]
MAX_PAGE_NUMBER = 10_000
MAX_PAGE_COPIES = 1_000
MAX_DIMENSION_MM = 2_000.0
MAX_FIELD_COUNT = 1_000
MAX_FIELD_NAME_LENGTH = 255
MAX_TEXT_LENGTH = 1_000_000
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_TOTAL_PAGE_COPIES = 1_000
MAX_TOTAL_FIELD_VALUES = 5_000
MAX_TOTAL_TEXT_CHARACTERS = 2_000_000
Scalar: TypeAlias = Annotated[str, Field(max_length=MAX_TEXT_LENGTH)] | int | float | bool
TextInput: TypeAlias = Scalar | Annotated[list[Scalar], Field(min_length=1, max_length=MAX_FIELD_COUNT)]


class TemplatePayloadError(ValueError):
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RepeatPage(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)

    page: int = Field(ge=1, le=MAX_PAGE_NUMBER)
    copies: int = Field(ge=1, le=MAX_PAGE_COPIES)


class ImageSpec(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)

    path: Path
    mode: ImageMode = "cell-fill"
    filloption: int = Field(default=5, ge=0, le=14)
    width_mm: float | None = Field(default=None, gt=0, le=MAX_DIMENSION_MM, allow_inf_nan=False)
    height_mm: float | None = Field(default=None, gt=0, le=MAX_DIMENSION_MM, allow_inf_nan=False)

    @field_validator("path")
    @classmethod
    def path_is_payload_relative(cls, path: Path) -> Path:
        raw = str(path)
        invalid_root = path.is_absolute() or bool(path.drive) or bool(path.root)
        if raw == "." or len(raw) > 1_024 or invalid_root or raw.startswith(("\\\\", "//")) or ".." in path.parts:
            raise TemplatePayloadError("이미지 경로는 payload 내부 상대 경로여야 합니다")
        return path

    @model_validator(mode="after")
    def dimensions_match_mode(self) -> ImageSpec:
        has_both = self.width_mm is not None and self.height_mm is not None
        has_any = self.width_mm is not None or self.height_mm is not None
        if self.mode == "inline" and not has_both:
            raise TemplatePayloadError("inline 이미지는 width_mm와 height_mm가 필요합니다")
        if self.mode == "cell-fill" and has_any:
            raise TemplatePayloadError("cell-fill 이미지에는 width_mm/height_mm를 지정할 수 없습니다")
        return self


ImageInput: TypeAlias = ImageSpec | Annotated[list[ImageSpec], Field(min_length=1, max_length=MAX_FIELD_COUNT)]


class ApplyPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)

    repeat_pages: tuple[RepeatPage, ...] = Field(default=(), max_length=MAX_FIELD_COUNT)
    text_fields: dict[str, TextInput] = Field(default_factory=dict, max_length=MAX_FIELD_COUNT)
    image_fields: dict[str, ImageInput] = Field(default_factory=dict, max_length=MAX_FIELD_COUNT)

    @model_validator(mode="after")
    def fields_are_usable(self) -> ApplyPayload:
        names = set(self.text_fields) | set(self.image_fields)
        if any(not name.strip() or len(name) > MAX_FIELD_NAME_LENGTH for name in names):
            raise TemplatePayloadError("필드명은 1~255자여야 합니다")
        overlap = set(self.text_fields) & set(self.image_fields)
        if overlap:
            raise TemplatePayloadError(f"텍스트와 이미지에 중복된 필드: {sorted(overlap)}")
        if sum(request.copies for request in self.repeat_pages) > MAX_TOTAL_PAGE_COPIES:
            raise TemplatePayloadError("전체 페이지 복사 횟수가 1,000회를 초과합니다")
        values = (*self.text_fields.values(), *self.image_fields.values())
        if sum(len(value) if isinstance(value, list) else 1 for value in values) > MAX_TOTAL_FIELD_VALUES:
            raise TemplatePayloadError("전체 텍스트/이미지 값이 5,000개를 초과합니다")
        characters = sum(len(_text(item)) for value in self.text_fields.values()
                         for item in (value if isinstance(value, list) else [value]))
        if characters > MAX_TOTAL_TEXT_CHARACTERS:
            raise TemplatePayloadError("전체 텍스트가 2,000,000자를 초과합니다")
        return self


def load_apply_payload(path: Path) -> ApplyPayload:
    try:
        if path.stat().st_size > MAX_PAYLOAD_BYTES:
            raise TemplatePayloadError("payload가 8 MiB를 초과합니다")
        raw = path.read_bytes()
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise TemplatePayloadError("읽는 중 payload가 8 MiB를 초과했습니다")
        return ApplyPayload.model_validate_json(raw)
    except (OSError, ValidationError, TemplatePayloadError) as error:
        raise DocumentAutomationError(f"적용 데이터 오류 ({path}): {error}") from error


def _field_count(hwp: HwpApplication, name: str) -> int:
    if not hwp.field_exist(name):
        return 0
    count = 0
    while count < 10_000 and hwp.move_to_field(name, idx=count, select=False):
        count += 1
    return count


def _text(value: Scalar) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def text_values(name: str, value: TextInput, count: int) -> tuple[str, ...]:
    if isinstance(value, list):
        values = tuple(_text(item) for item in value)
    else:
        values = (_text(value),)
    if len(values) != count:
        raise DocumentAutomationError(f"필드 개수 불일치 ({name}): 문서 {count}개, 데이터 {len(values)}개")
    return values


def image_values(name: str, value: ImageInput, count: int) -> tuple[ImageSpec, ...]:
    values = tuple(value) if isinstance(value, list) else (value,)
    if len(values) != count:
        raise DocumentAutomationError(f"이미지 필드 개수 불일치 ({name}): 문서 {count}개, 데이터 {len(values)}개")
    return values


def _local_file(base: Path, path: Path) -> Path:
    root = base.expanduser().resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root) or str(resolved).startswith("\\\\") or not resolved.is_file():
        raise DocumentAutomationError(f"로컬 이미지 파일이 없습니다: {resolved}")
    return resolved


@dataclass(frozen=True, slots=True)
class _PreparedImage:
    spec: ImageSpec
    path: Path


PreparedImageFields: TypeAlias = tuple[tuple[str, tuple[_PreparedImage, ...]], ...]
TextFieldPlan: TypeAlias = tuple[tuple[str, tuple[str, ...]], ...]


@runtime_checkable
class _MillimeterConverter(Protocol):
    def MiliToHwpUnit(self, value: float) -> float: ...


def _prepare_image_files(fields: dict[str, ImageInput], base: Path) -> PreparedImageFields:
    prepared: list[tuple[str, tuple[_PreparedImage, ...]]] = []
    for name, value in fields.items():
        specs = tuple(value) if isinstance(value, list) else (value,)
        prepared.append((name, tuple(_PreparedImage(spec, _local_file(base, spec.path)) for spec in specs)))
    return tuple(prepared)


def _ensure_fields_exist(hwp: HwpApplication, payload: ApplyPayload) -> None:
    for name in payload.text_fields:
        if not hwp.field_exist(name):
            raise DocumentAutomationError(f"문서에 없는 텍스트 필드: {name}")
    for name in payload.image_fields:
        if not hwp.field_exist(name):
            raise DocumentAutomationError(f"문서에 없는 이미지 필드: {name}")


def _prepare_text_values(hwp: HwpApplication, fields: dict[str, TextInput]) -> TextFieldPlan:
    return tuple((name, text_values(name, value, _field_count(hwp, name)))
                 for name, value in fields.items())


def _validate_image_counts(
    hwp: HwpApplication, fields: PreparedImageFields
) -> PreparedImageFields:
    for name, values in fields:
        count = _field_count(hwp, name)
        if len(values) != count:
            raise DocumentAutomationError(
                f"이미지 필드 개수 불일치 ({name}): 문서 {count}개, 데이터 {len(values)}개"
            )
    return fields


def repeat_pages(hwp: HwpApplication, requests: tuple[RepeatPage, ...]) -> None:
    for request in sorted(requests, key=lambda item: item.page, reverse=True):
        if request.page > int(hwp.PageCount):
            raise DocumentAutomationError(
                f"복제할 페이지가 문서 범위를 벗어났습니다: {request.page}"
            )
        for _ in range(request.copies):
            _ = hwp.goto_page(request.page)
            if not hwp.CopyPage():
                raise DocumentAutomationError(f"페이지 복사 실패: {request.page}")
            if not hwp.PastePage():
                raise DocumentAutomationError(f"페이지 붙여넣기 실패: {request.page}")
    if requests:
        hwp.RecalcPageCount()


def fill_text_fields(hwp: HwpApplication, fields: dict[str, TextInput]) -> None:
    for name, value in fields.items():
        count = _field_count(hwp, name)
        if count == 0:
            raise DocumentAutomationError(f"문서에 없는 텍스트 필드: {name}")
        for index, text in enumerate(text_values(name, value, count)):
            hwp.put_field_text(name, text, idx=index)


def _insert_image(
    hwp: HwpApplication, name: str, index: int, prepared: _PreparedImage
) -> None:
    spec = prepared.spec
    hwp.put_field_text(name, "", idx=index)
    if not hwp.move_to_field(name, idx=index, select=False):
        raise DocumentAutomationError(f"이미지 필드로 이동 실패: {name}[{index}]")
    if spec.mode == "cell-fill":
        if not hwp.is_cell():
            raise DocumentAutomationError(f"cell-fill 필드가 표 셀 안에 없습니다: {name}")
        _ = hwp.TableCellBlock()
        try:
            inserted = hwp.insert_background_picture(
                str(prepared.path),
                border_type="SelectedCell",
                embedded=True,
                filloption=spec.filloption,
            )
        finally:
            _ = hwp.Cancel()
        if not inserted:
            raise DocumentAutomationError(f"셀 이미지 삽입 실패: {name}[{index}]")
        return

    width_mm = spec.width_mm
    height_mm = spec.height_mm
    if width_mm is None or height_mm is None:
        raise DocumentAutomationError("inline 이미지 크기 계약이 손상되었습니다")
    if not isinstance(hwp, _MillimeterConverter):
        raise DocumentAutomationError("그림 크기 단위를 변환할 수 없습니다")
    control = hwp.insert_picture(
        str(prepared.path),
        treat_as_char=True,
        embedded=True,
        sizeoption=1,
        width=round(width_mm),
        height=round(height_mm),
    )
    enforce_picture_size(
        hwp,
        control,
        width_mm=width_mm,
        height_mm=height_mm,
    )


def _fill_image_fields(
    hwp: HwpApplication, fields: PreparedImageFields
) -> None:
    for name, values in fields:
        for index, prepared in enumerate(values):
            _insert_image(hwp, name, index, prepared)


def apply_template(template: Path, data: Path, output: Path, *, visible: bool) -> Path:
    source = input_document(template)
    destination = output_document(output)
    if source == destination:
        raise DocumentAutomationError("원본 문서를 덮어쓸 수 없습니다")
    payload_path = data.expanduser().resolve()
    payload = load_apply_payload(payload_path)
    prepared_images = _prepare_image_files(payload.image_fields, payload_path.parent)
    with HwpSession(visible=visible) as hwp:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not hwp.open(str(source)):
            raise DocumentAutomationError(f"한컴 문서 열기 실패: {source}")
        _ensure_fields_exist(hwp, payload)
        repeat_pages(hwp, payload.repeat_pages)
        text_plan = _prepare_text_values(hwp, payload.text_fields)
        image_plan = _validate_image_counts(hwp, prepared_images)
        for name, values in text_plan:
            for index, text in enumerate(values):
                hwp.put_field_text(name, text, idx=index)
        _fill_image_fields(hwp, image_plan)
        if not hwp.save_as(str(destination), format=destination.suffix[1:].upper()):
            raise DocumentAutomationError(f"한컴 문서 저장 실패: {destination}")
    return destination
