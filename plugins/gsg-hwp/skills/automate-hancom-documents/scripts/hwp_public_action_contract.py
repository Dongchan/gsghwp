from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — public action schemas and their opaque target store form one boundary.

import ntpath
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Final, Literal, Protocol, final
from uuid import uuid4

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from hwp_color_normalization import ColorInput, canonical_rgb_hex
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


def _document_path_key(document_path: str | None) -> str | None:
    if not document_path:
        return None
    return ntpath.normcase(ntpath.normpath(document_path))


def _same_document_path(first: str | None, second: str | None) -> bool:
    return _document_path_key(first) == _document_path_key(second)


type PublicImageInsertTarget = Literal["selection", "document_end"]
type PublicObjectKind = Literal["table", "picture"]
type PublicTextAlignment = Literal["left", "center", "right", "justify", "inherit"]
type PublicStyleId = Annotated[int, Field(ge=0, le=4_095)]
# 250 x 350 은 네이티브 한계가 아니다. INSERT_PICTURE 는 ActionProtocol.cpp:391
# 에서 ParsePositiveMillimeter 로 읽고, 그 상한은 ActionProtocol.cpp:136 의
# 1,000mm 다. 그래서 A3(297x420) 같은 큰 용지에서 250x350 을 넘는 그림이 막힌다.
# 다만 여기만 올릴 수는 없다. hwp_insert_image / hwp_replace_image 는 같은 값을
# hwp_priority_recipe_contract.py:29-30 의 picture_width_mm / picture_height_mm
# (역시 250 / 350)로 그대로 넘긴다. 여기만 풀면 스키마 거부가 도구 안쪽의
# ValidationError 로 바뀔 뿐이라 상황이 더 나빠진다. 두 곳을 함께 올려야 한다.
type PublicImageWidth = Annotated[float, Field(ge=1, le=250)]
type PublicImageHeight = Annotated[float, Field(ge=1, le=350)]
type PublicFontName = Annotated[str, Field(min_length=1, max_length=100)]
type PublicFontSize = Annotated[float, Field(ge=1, le=96)]
type PublicTextColor = ColorInput
type PublicLineSpacing = Annotated[int, Field(ge=50, le=500)]
type PublicReplacementText = Annotated[str, Field(max_length=1_000_000)]
type PublicExpectedText = Annotated[str, Field(max_length=1_000_000)]
type PublicOperationId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        description="논리 작업별 고유 ID. 같은 payload 재시도에만 재사용합니다.",
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

    async def inspect_page_fast(
        self,
        document_selector: str | None,
        page: int,
        include_cells: bool,
    ) -> FastPageInspection: ...


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
    # 상한 20,000 은 ActionTextPatch.cpp:793 kMaximumSearches 에서 왔다.
    # SelectTextPatchMatch 의 ForwardFind 루프(ActionTextPatch.cpp:815)가 정확히
    # kMaximumSearches 회만 돌고, 한 회가 최대 한 건을 세므로 도달 가능한
    # occurrence 는 20,000 이 끝이다. 그 위는 항상 TEXT_SEARCH_LIMIT 으로 돌아온다.
    occurrence: int | None = Field(default=None, ge=1, le=20_000)
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
            parameters["text_color"] = canonical_rgb_hex(self.text_color)
        if self.alignment != "inherit":
            parameters["alignment"] = self.alignment
        if self.line_spacing is not None:
            parameters["line_spacing"] = self.line_spacing
        return parameters

    def to_live(self) -> TextFormatSpec:
        return TextFormatSpec(
            self.bold,
            self.font_name,
            self.font_size_pt,
            self.text_color,
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
    __slots__ = ("_inspected_document", "_inspected_targets", "_targets")

    def __init__(self) -> None:
        self._targets: dict[str, PublicObjectTargetReference] = {}
        self._inspected_targets: dict[str, PublicObjectTargetReference] = {}
        self._inspected_document: tuple[int, str | None] | None = None

    def _clear_targets(self) -> None:
        self._targets.clear()
        self._inspected_targets.clear()

    def _prepare_candidate_document(self, document_path: str | None) -> None:
        inspected_document = self._inspected_document
        candidate_path = _document_path_key(document_path)
        if (
            inspected_document is not None
            and candidate_path is not None
            and inspected_document[1] != candidate_path
        ):
            self._clear_targets()
            self._inspected_document = None

    def _prepare_inspection(self, inspection: FastPageInspection) -> None:
        document = (
            inspection.document_id,
            _document_path_key(inspection.full_name),
        )
        changed_document = (
            self._inspected_document is not None
            and self._inspected_document != document
        )
        if self._inspected_document is None and self._targets:
            candidate_paths = {
                _document_path_key(target.document_path)
                for target in self._targets.values()
            }
            changed_document = candidate_paths != {document[1]}
        if changed_document:
            self._clear_targets()
        self._inspected_document = document

    def _remember_inspected(
        self,
        instance_id: str,
        target: PublicObjectTargetReference,
    ) -> None:
        _ = self._inspected_targets.pop(instance_id, None)
        self._inspected_targets[instance_id] = target
        while len(self._inspected_targets) > _MAX_INSPECTED_TARGETS:
            oldest = next(iter(self._inspected_targets))
            del self._inspected_targets[oldest]

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
        if reference is None:
            if target_id.startswith("hwp-target-"):
                return None
            return CanonicalPublicObjectTarget(
                document_path,
                HwpOperateTarget(
                    kind="picture",
                    binding="active",
                    match_policy="return_candidates",
                    control_instance_id=target_id,
                ),
            )
        if reference.kind != "picture":
            return None
        if (
            document_path is not None
            and reference.document_path is not None
            and not _same_document_path(reference.document_path, document_path)
        ):
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
            if target_id.startswith("hwp-target-"):
                return None
            return CanonicalPublicObjectTarget(
                document_path,
                HwpOperateTarget(
                    kind="control",
                    binding="active",
                    match_policy="return_candidates",
                    control_instance_id=target_id,
                ),
            )
        if (
            document_path is not None
            and reference.document_path is not None
            and not _same_document_path(reference.document_path, document_path)
        ):
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
        self._prepare_candidate_document(document_path)
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
        self._prepare_inspection(inspection)
        counts: dict[PublicObjectKind, int] = {"table": 0, "picture": 0}
        remembered = 0
        for control in inspection.controls:
            kind = _NATIVE_CONTROL_KINDS.get(control.control_type)
            if kind is None:
                continue
            remembered += 1
            if remembered > _MAX_INSPECTED_TARGETS:
                break
            counts[kind] += 1
            self._remember_inspected(
                control.instance_id,
                PublicObjectTargetReference(
                    inspection.full_name,
                    kind,
                    inspection.page,
                    counts[kind],
                    control.instance_id,
                ),
            )

    def clear(self) -> None:
        self._clear_targets()
        self._inspected_document = None
