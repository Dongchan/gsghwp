from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import Final

from pydantic import Field

from hwp_live_values import ContractModel
from hwp_mcp_registry import (
    CapabilityCategory,
    CapabilityOperation,
    ExecutionPath,
    McpProfile,
    tool_specs,
)


# ---------------------------------------------------------------------------
# Per-capability bridge protocol requirements (internal).
#
# `NativeCapabilityInventory.native_protocol_required` is the public
# `hwp_get_capabilities` response and is a single flat integer, so it cannot
# carry one number per capability without changing that public schema. The real
# per-capability requirements are therefore modelled here, and the public field
# reports their maximum: the bridge protocol needed to run the whole advertised
# feature set. The previous hardcoded 9 under-stated that, which advertised
# features requiring 10, 11, and 12 as usable on a protocol-9 bridge.
#
# Every entry below is grounded in addon/HancomLiveBridgeNative (read-only) or
# in the gate actually enforced on that capability's execution path.
# ---------------------------------------------------------------------------

# Capabilities whose execution path never enters the native mutation engine.
_NON_NATIVE_PROTOCOL: Final = 1
_NON_NATIVE_EXECUTION_PATHS: Final[frozenset[str]] = frozenset(
    {
        "python_catalog",
        "mcp_forward",
        "win32_ui",
        "com_dispatch",
        "rot_com",
    }
)

# Native paths that use no bridge feature newer than the atomic action batch.
# Mirrors ATOMIC_ACTION_MINIMUM_NATIVE_PROTOCOL
# (hwp_native_failure_result.py:11), which is the gate those paths pass to
# execute_native_actions.
_NATIVE_BASELINE_PROTOCOL: Final = 9

_CAPABILITY_PROTOCOL_REQUIREMENTS: Final[Mapping[str, int]] = {
    # README: "Protocol 12 separates non-destructive SaveVerify from the
    # explicit SaveReopenVerify diagnostic and adds full text/document
    # fingerprints plus failed-reopen session recovery."
    # SaveVerify is the protocol-12 method behind execute_native_save; its
    # HLS1 response is emitted at DocumentLifecycle.cpp:273.
    "save": 12,
    # Same README entry. decode_lifecycle_result requires the HCL12 shape
    # emitted at DocumentLifecycle.cpp:429, including the recovery and
    # fingerprint fields protocol 12 introduced.
    "save_reopen_verify": 12,
    # README: "Protocol 9 adds bounded document checkpoints for logical MCP
    # undo/redo around control and page deletion." Capturing a checkpoint gates
    # at 9, but replaying one uses RestoreDocumentFileCommand, which gates at 12
    # (hwp_live_edit_history_runtime.py:145 via _restore_checkpoint, reached by
    # execute_document_edit_history). Undo/redo therefore need 12.
    "undo": 12,
    "redo": 12,
    # Deletion only captures the protocol-9 checkpoint; the protocol-12 restore
    # happens on a later undo, which is charged to undo/redo above.
    "delete_page": 9,
    "delete_control": 9,
    # README: "Protocol 11 adds atomic PATCH_TEXT targeting the current
    # cursor/selection, an exact native range, an unambiguous search result, or
    # a table cell." Gate: hwp_live_session_structure_mutation.py:265.
    "patch_text": 11,
    # README: "Protocol 10 adds ReferenceLayoutBulk inside the same
    # ExecuteActions mutation engine." Both layout tools document the split
    # themselves: independent elements use "Protocol 9 ApplyLayout Bulk" and a
    # page-dominating adaptive grid uses "Protocol 10 ReferenceLayoutBulk"
    # (hwp_public_action_metadata.py:69). The capability requirement is the
    # higher of the two paths it exposes.
    "append_layout": 10,
    "insert_layout": 10,
    # QA-only tool on the same ReferenceLayoutBulk path.
    "apply_layout": 10,
}


@cache
def _execution_paths() -> Mapping[str, ExecutionPath]:
    return {
        spec.name.removeprefix("hwp_"): spec.execution_path
        for profile in ("production", "qa")
        for spec in tool_specs(profile)
    }


def capability_protocol_requirement(capability_id: str) -> int:
    """Minimum bridge protocol this one capability needs.

    Internal detail: the public inventory reports only the maximum across the
    advertised set, so this granularity never reaches the MCP schema.
    """
    requirement = _CAPABILITY_PROTOCOL_REQUIREMENTS.get(capability_id)
    if requirement is not None:
        return requirement
    execution_path = _execution_paths().get(capability_id)
    if execution_path is not None and execution_path in _NON_NATIVE_EXECUTION_PATHS:
        return _NON_NATIVE_PROTOCOL
    # Unknown ids fall back to the native baseline rather than the non-native
    # floor, so an unmapped capability can never under-state its requirement.
    return _NATIVE_BASELINE_PROTOCOL


class NativeCapability(ContractModel):
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    category: CapabilityCategory
    operation: CapabilityOperation
    execution_path: ExecutionPath
    tool_names: tuple[str, ...] = Field(min_length=1)
    requires_session: bool
    requires_state_token: bool = False


class NativeCapabilityInventory(ContractModel):
    native_protocol_required: int = Field(ge=1)
    capabilities: tuple[NativeCapability, ...]


def native_capability_inventory(
    profile: McpProfile = "production",
) -> NativeCapabilityInventory:
    capabilities = tuple(
        NativeCapability(
            capability_id=spec.name.removeprefix("hwp_"),
            category=spec.category,
            operation=spec.operation,
            execution_path=spec.execution_path,
            tool_names=(spec.name,),
            requires_session=spec.requires_session,
            requires_state_token=spec.requires_state_token,
        )
        for spec in tool_specs(profile)
    )
    return NativeCapabilityInventory(
        native_protocol_required=max(
            capability_protocol_requirement(capability.capability_id)
            for capability in capabilities
        ),
        capabilities=capabilities,
    )
