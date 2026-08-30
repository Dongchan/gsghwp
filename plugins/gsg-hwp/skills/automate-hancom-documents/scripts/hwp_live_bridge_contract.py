from __future__ import annotations

from pydantic import Field

from hwp_live_contract import LiveContext
from hwp_live_structure_contract import DocumentStructure
from hwp_live_values import ContractModel


class HancomDialogControlState(ContractModel):
    window_handle: int = Field(ge=1)
    title: str = Field(max_length=4_000)
    class_name: str = Field(max_length=200)
    control_id: int = Field(ge=-1, le=65_535)
    style: int
    visible: bool
    enabled: bool
    source: str = Field(default="win32", max_length=20)
    automation_id: str | None = Field(default=None, max_length=500)
    control_type: str | None = Field(default=None, max_length=100)
    focused: bool = False
    role: str = Field(max_length=50)
    actionable: bool
    default: bool
    accelerator: str | None = Field(default=None, max_length=8)


class HancomPopupStructure(ContractModel):
    window_handle: int = Field(ge=1)
    process_id: int = Field(ge=0)
    owner_handle: int = Field(ge=0)
    root_owner_handle: int = Field(ge=0)
    title: str = Field(max_length=1_000)
    class_name: str = Field(max_length=200)
    visible: bool
    enabled: bool
    controls: tuple[HancomDialogControlState, ...]
    default_button_id: int | None = Field(default=None, ge=0, le=65_535)
    structure_complete: bool


class HancomDialogActionResult(ContractModel):
    before: HancomPopupStructure
    selected_control: HancomDialogControlState
    message_delivered: bool
    dialog_present_after: bool
    after: HancomPopupStructure | None


class HancomDialogState(ContractModel):
    window_handle: int = Field(ge=1)
    owner_handle: int = Field(ge=0)
    title: str = Field(max_length=1_000)
    class_name: str = Field(max_length=200)
    visible: bool
    enabled: bool
    modal: bool


class HancomWindowChildState(ContractModel):
    window_handle: int = Field(ge=1)
    title: str = Field(max_length=4_000)
    class_name: str = Field(max_length=200)
    visible: bool
    enabled: bool


class HancomWindowState(ContractModel):
    window_handle: int = Field(ge=1)
    process_id: int = Field(ge=0)
    exists: bool
    visible: bool
    enabled: bool
    foreground: bool
    title: str = Field(max_length=1_000)
    class_name: str = Field(max_length=200)
    dialogs: tuple[HancomDialogState, ...]
    children: tuple[HancomWindowChildState, ...] = ()


class HancomWindowStateList(ContractModel):
    windows: tuple[HancomWindowState, ...]


class HancomDialogDismissResult(ContractModel):
    before: HancomWindowState
    after: HancomWindowState
    dismissed_handles: tuple[int, ...]


class BridgeSnapshot(ContractModel):
    context: LiveContext
    structure: DocumentStructure
    window: HancomWindowState


class BridgeState(ContractModel):
    revision: int = Field(ge=1)
    previous_revision: int = Field(ge=0)
    full_snapshot: bool
    changed_paths: tuple[str, ...]
    snapshot: BridgeSnapshot
