from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — public action schemas and their opaque target store form one boundary.

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Final, Literal, Protocol, final
from uuid import uuid4

from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from hwp_color_normalization import canonical_rgb_hex, parse_rgb_color
from hwp_live_structure_contract import FastPageInspection
from hwp_live_native_action_models import NativePosition
from hwp_live_native_format_inputs import TextFormatSpec
from hwp_live_text_patch_contract import TextPatchRequest, TextPatchTarget
from hwp_live_values import ContractModel
from hwp_operation_contract import (
    HwpOperateGuards,
    HwpOperateInputs,
    HwpOperateTarget,
    OperationInputValue,
    OperationResult,
    WorkflowTargetCandidate,
)


_MAX_INSPECTED_TARGETS: Final = 256


type PublicImageInsertTarget = Literal["selection", "document_end"]
type PublicObjectKind = Literal["table", "picture"]
type PublicTextAlignment = Literal["left", "center", "right", "justify", "inherit"]
type PublicStyleId = Annotated[int, Field(ge=0, le=4_095)]
type PublicImageWidth = Annotated[float, Field(ge=1, le=250)]
type PublicImageHeight = Annotated[float, Field(ge=1, le=350)]
type PublicFontName = Annotated[str, Field(min_length=1, max_length=100)]
type PublicFontSize = Annotated[float, Field(ge=1, le=96)]
type PublicTextColor = Annotated[str, Field(min_length=1, max_length=50)]
type PublicLineSpacing = Annotated[int, Field(ge=50, le=500)]
type PublicReplacementText = Annotated[str, Field(max_length=1_000_000)]
type PublicExpectedText = Annotated[str, Field(max_length=1_000_000)]
type PublicOperationId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        description=(
            "호출자가 논리 작업마다 한 번 생성하고 응답 유실 후 재시도할 때 "
            "그대로 재사용하는 안정 ID. 저널 보존 만료 후에도 다른 작업에 재사용하지 않습니다"
        ),
    ),
]

_NATIVE_CONTROL_KINDS: Final[Mapping[str, PublicObjectKind]] = MappingProxyType(
    {
        "tbl": "table",
        "gso": "picture",
        "pic": "picture",
        "picture": "picture",
    }
)
OBJECT_INPUT_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "inputs.assets.images": "path",
        "inputs.recipe.caption_text": "text",
        "inputs.target": "target_id",
        "inputs.target.control_instance_id": "target_id",
    }
)
SELECTION_INPUT_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "inputs.parameters": "formatting",
        "inputs.recipe.target_position": "current_selection",
        "inputs.target": "current_selection",
    }
)
DOCUMENT_INPUT_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {"inputs.layout": "layout"}
)


class PublicActionExecutor(Protocol):
    async def execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationResult: ...


class PublicSelectionExecutor(PublicActionExecutor, Protocol):
    async def replace_selected_text(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        replacement: str,
    ) -> OperationResult: ...

    async def patch_text(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        request: TextPatchRequest,
    ) -> OperationResult: ...


class PublicTextPatchPosition(ContractModel):
    list_id: int = Field(ge=0)
    paragraph: int = Field(ge=0)
    character: int = Field(ge=0)

    def to_native(self) -> NativePosition:
        return NativePosition(self.list_id, self.paragraph, self.character)


class PublicTextPatchTarget(ContractModel):
    kind: Literal["current", "range", "find", "table_cell"] = "current"
    start: PublicTextPatchPosition | None = None
    end: PublicTextPatchPosition | None = None
    occurrence: int | None = Field(default=None, ge=1, le=1_000_000)
    match_case: bool = False
    table_instance_id: str | None = Field(default=None, min_length=1, max_length=100)
    cell: str | None = Field(default=None, pattern=r"^[A-Za-z]+[1-9][0-9]*$")

    @model_validator(mode="after")
    def validate_target_fields(self) -> PublicTextPatchTarget:
        range_fields = self.start is not None or self.end is not None
        search_fields = self.occurrence is not None or self.match_case
        cell_fields = self.table_instance_id is not None or self.cell is not None
        valid = {
            "current": not range_fields and not search_fields and not cell_fields,
            "range": self.start is not None
            and self.end is not None
            and not search_fields
            and not cell_fields,
            "find": not range_fields and not cell_fields,
            "table_cell": not range_fields
            and self.table_instance_id is not None
            and self.cell is not None,
        }[self.kind]
        if not valid:
            raise PydanticCustomError(
                "public_text_patch_target",
                "text.patch 대상 종류와 좌표·검색·표 셀 입력 조합이 올바르지 않습니다",
            )
        if (
            self.kind == "range"
            and self.start is not None
            and self.end is not None
            and self.start.list_id != self.end.list_id
        ):
            raise PydanticCustomError(
                "public_text_patch_cross_control",
                "text.patch 범위는 서로 다른 한컴 컨트롤을 가로지를 수 없습니다",
            )
        return self

    def to_live(self) -> TextPatchTarget:
        return TextPatchTarget(
            kind=self.kind,
            start=None if self.start is None else self.start.to_native(),
            end=None if self.end is None else self.end.to_native(),
            occurrence=self.occurrence,
            match_case=self.match_case,
            table_instance_id=self.table_instance_id,
            cell_address=None if self.cell is None else self.cell.upper(),
        )


class PublicImageSize(ContractModel):
    width_mm: PublicImageWidth | None = None
    height_mm: PublicImageHeight | None = None

    @model_validator(mode="after")
    def require_complete_size(self) -> PublicImageSize:
        if (self.width_mm is None) != (self.height_mm is None):
            raise PydanticCustomError(
                "public_image_size_pair",
                "width_mm와 height_mm는 함께 전달해야 합니다",
            )
        return self


class PublicTextFormattingInput(ContractModel):
    bold: bool | None = None
    font_name: PublicFontName | None = None
    font_size_pt: PublicFontSize | None = None
    text_color: PublicTextColor | None = None
    alignment: PublicTextAlignment = "inherit"
    line_spacing: PublicLineSpacing | None = None

    @field_validator("text_color")
    @classmethod
    def normalize_text_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = parse_rgb_color(value)
        if parsed is None:
            raise PydanticCustomError(
                "public_text_color",
                "글자색은 이름, #RRGGBB, rgb(r,g,b), 또는 r,g,b 형식이어야 합니다",
            )
        return canonical_rgb_hex(parsed)

    @model_validator(mode="after")
    def require_format(self) -> PublicTextFormattingInput:
        if (
            all(
                value is None
                for value in (
                    self.bold,
                    self.font_name,
                    self.font_size_pt,
                    self.text_color,
                    self.line_spacing,
                )
            )
            and self.alignment == "inherit"
        ):
            raise PydanticCustomError(
                "public_text_format_required",
                "적용할 글자 또는 문단 서식이 필요합니다",
            )
        return self

    def to_parameters(self) -> Mapping[str, OperationInputValue]:
        parameters: dict[str, OperationInputValue] = {}
        if self.bold is not None:
            parameters["bold"] = self.bold
        if self.font_name is not None:
            parameters["font_name"] = self.font_name
        if self.font_size_pt is not None:
            parameters["font_size_pt"] = self.font_size_pt
        if self.text_color is not None:
            parameters["text_color"] = self.text_color
        if self.alignment != "inherit":
            parameters["alignment"] = self.alignment
        if self.line_spacing is not None:
            parameters["line_spacing"] = self.line_spacing
        return parameters

    def to_live(self) -> TextFormatSpec:
        color = None if self.text_color is None else parse_rgb_color(self.text_color)
        if self.text_color is not None and color is None:
            raise RuntimeError("검증된 공개 글자색을 내부 RGB로 변환하지 못했습니다")
        return TextFormatSpec(
            self.bold,
            self.font_name,
            self.font_size_pt,
            color,
            self.alignment,
            self.line_spacing,
        )


@dataclass(frozen=True, slots=True)
class CanonicalPublicObjectTarget:
    document_path: str | None
    target: HwpOperateTarget


@dataclass(frozen=True, slots=True)
class PublicObjectTargetReference:
    document_path: str | None
    kind: PublicObjectKind
    page: int
    index: int
    control_instance_id: str | None = None


@final
class PublicObjectTargetStore:
    __slots__ = ("_inspected_targets", "_targets")

    def __init__(self) -> None:
        self._targets: dict[str, PublicObjectTargetReference] = {}
        self._inspected_targets: dict[str, PublicObjectTargetReference] = {}

    def _reference(self, target_id: str) -> PublicObjectTargetReference | None:
        return self._targets.get(target_id) or self._inspected_targets.get(target_id)

    def resolve_picture(
        self,
        target_id: str | None,
        document_path: str | None,
    ) -> CanonicalPublicObjectTarget | None:
        if target_id is None:
            return CanonicalPublicObjectTarget(
                document_path,
                HwpOperateTarget(
                    kind="picture",
                    binding="selection",
                    match_policy="return_candidates",
                ),
            )
        reference = self._reference(target_id)
        if reference is None or reference.kind != "picture":
            return None
        if document_path is not None and reference.document_path != document_path:
            return None
        return CanonicalPublicObjectTarget(
            reference.document_path,
            HwpOperateTarget(
                kind="picture",
                binding="active",
                page_hint=reference.page,
                table_index=reference.index,
                match_policy="return_candidates",
                control_instance_id=reference.control_instance_id,
            ),
        )

    def resolve_control(
        self,
        target_id: str | None,
        document_path: str | None,
    ) -> CanonicalPublicObjectTarget | None:
        if target_id is None:
            return CanonicalPublicObjectTarget(
                document_path,
                HwpOperateTarget(
                    kind="control",
                    binding="selection",
                    match_policy="return_candidates",
                ),
            )
        reference = self._reference(target_id)
        if reference is None:
            return None
        if document_path is not None and reference.document_path != document_path:
            return None
        return CanonicalPublicObjectTarget(
            reference.document_path,
            HwpOperateTarget(
                kind="control",
                binding="active",
                page_hint=reference.page,
                table_index=reference.index,
                match_policy="return_candidates",
                control_instance_id=reference.control_instance_id,
            ),
        )

    def remember(
        self,
        candidates: tuple[WorkflowTargetCandidate, ...],
        document_path: str | None,
    ) -> tuple[str, ...]:
        if not candidates:
            return ()
        self._targets.clear()
        target_ids: list[str] = []
        for candidate in candidates[:3]:
            if candidate.kind == "picture":
                index = candidate.picture_index
            else:
                index = candidate.table_index
            assert index is not None
            target_id = f"hwp-target-{uuid4().hex}"
            self._targets[target_id] = PublicObjectTargetReference(
                document_path,
                candidate.kind,
                candidate.page,
                index,
            )
            target_ids.append(target_id)
        return tuple(target_ids)

    def remember_inspection(self, inspection: FastPageInspection) -> None:
        self._inspected_targets.clear()
        counts: dict[PublicObjectKind, int] = {"table": 0, "picture": 0}
        for control in inspection.controls[:_MAX_INSPECTED_TARGETS]:
            kind = _NATIVE_CONTROL_KINDS.get(control.control_type)
            if kind is None:
                continue
            counts[kind] += 1
            self._inspected_targets[control.instance_id] = PublicObjectTargetReference(
                inspection.full_name,
                kind,
                inspection.page,
                counts[kind],
                control.instance_id,
            )

    def clear(self) -> None:
        self._targets.clear()
        self._inspected_targets.clear()
