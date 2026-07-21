from __future__ import annotations

from pydantic import Field

from hwp_live_contract import LiveContext
from hwp_live_structure_contract import DocumentStructure
from hwp_live_values import ContractModel


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
