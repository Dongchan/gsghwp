from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class NativePosition:
    list_id: int
    paragraph: int
    character: int


@dataclass(frozen=True, slots=True)
class NativeSelection:
    selected: bool
    start: NativePosition
    end: NativePosition


@dataclass(frozen=True, slots=True)
class NativeCharacterFormat:
    face_name: str
    height_hwpunit: int
    bold: bool
    text_color: int


@dataclass(frozen=True, slots=True)
class NativeParagraphFormat:
    alignment: int
    line_spacing: int
    left_margin_hwpunit: int
    right_margin_hwpunit: int
    indentation_hwpunit: int
    previous_spacing_hwpunit: int
    next_spacing_hwpunit: int


@dataclass(frozen=True, slots=True)
class IntegerValue:
    value: int


@dataclass(frozen=True, slots=True)
class BooleanValue:
    value: bool


@dataclass(frozen=True, slots=True)
class TextValue:
    value: str


@dataclass(frozen=True, slots=True)
class MillimeterValue:
    value: float


@dataclass(frozen=True, slots=True)
class EnumerationValue:
    converter: str
    value: str


type NativeActionValue = (
    IntegerValue | BooleanValue | TextValue | MillimeterValue | EnumerationValue
)


@dataclass(frozen=True, slots=True)
class NativeSetter:
    path: str
    value: NativeActionValue


@dataclass(frozen=True, slots=True)
class NativeArray:
    name: str
    count: int


@dataclass(frozen=True, slots=True)
class NativeArrayValue:
    name: str
    index: int
    value: NativeActionValue


@dataclass(frozen=True, slots=True)
class RunCommand:
    action: str


@dataclass(frozen=True, slots=True)
class ParameterActionCommand:
    action: str
    parameter_set: str
    setters: tuple[NativeSetter, ...] = ()
    arrays: tuple[NativeArray, ...] = ()
    array_values: tuple[NativeArrayValue, ...] = ()


@dataclass(frozen=True, slots=True)
class CallCommand:
    method: str
    arguments: tuple[NativeActionValue, ...] = ()


@dataclass(frozen=True, slots=True)
class MovePageCommand:
    page: int


@dataclass(frozen=True, slots=True)
class MovePositionCommand:
    position: NativePosition


@dataclass(frozen=True, slots=True)
class SelectControlCommand:
    instance_id: str


@dataclass(frozen=True, slots=True)
class DeleteControlCommand:
    instance_id: str


@dataclass(frozen=True, slots=True)
class CopyControlCommand:
    instance_id: str


@dataclass(frozen=True, slots=True)
class SaveDocumentFileCommand:
    path: Path


@dataclass(frozen=True, slots=True)
class ApplyCopiedTableAnchorCommand:
    instance_id: str


@dataclass(frozen=True, slots=True)
class PasteTableCommand:
    pass


@dataclass(frozen=True, slots=True)
class CaptureTableCommand:
    pass


@dataclass(frozen=True, slots=True)
class MoveDocumentEndCommand:
    pass


@dataclass(frozen=True, slots=True)
class DeleteTailCommand:
    start: NativePosition
    expected_prefix: str


@dataclass(frozen=True, slots=True)
class InsertTextCommand:
    text: str


@dataclass(frozen=True, slots=True)
class ReplaceSelectionCommand:
    expected_text: str
    replacement: str


@dataclass(frozen=True, slots=True)
class InsertPictureCommand:
    path: Path
    width_mm: float | None = None
    height_mm: float | None = None


@dataclass(frozen=True, slots=True)
class CellCommand:
    address: str


@dataclass(frozen=True, slots=True)
class SetCellTextCommand:
    address: str
    text: str


@dataclass(frozen=True, slots=True)
class MergeCommand:
    first: str
    second: str


@dataclass(frozen=True, slots=True)
class CaptionCommand:
    style_id: int
    title: str
    character_format: NativeCharacterFormat | None = None
    paragraph_format: NativeParagraphFormat | None = None
    format_source: NativePosition | None = None


@dataclass(frozen=True, slots=True)
class LeaveTableCommand:
    pass


type NativeActionCommand = (
    RunCommand
    | ParameterActionCommand
    | CallCommand
    | MovePageCommand
    | MovePositionCommand
    | SelectControlCommand
    | DeleteControlCommand
    | CopyControlCommand
    | SaveDocumentFileCommand
    | ApplyCopiedTableAnchorCommand
    | PasteTableCommand
    | CaptureTableCommand
    | MoveDocumentEndCommand
    | DeleteTailCommand
    | InsertTextCommand
    | ReplaceSelectionCommand
    | InsertPictureCommand
    | CellCommand
    | SetCellTextCommand
    | MergeCommand
    | CaptionCommand
    | LeaveTableCommand
)


@dataclass(frozen=True, slots=True)
class NativeActionRequest:
    document_id: int
    full_name: str
    commands: tuple[NativeActionCommand, ...]
    expected_cursor: NativePosition | None = None
    expected_selection: NativeSelection | None = None
    atomic: bool = False
