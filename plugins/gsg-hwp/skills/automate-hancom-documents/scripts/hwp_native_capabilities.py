from __future__ import annotations

from pydantic import Field

from hwp_live_values import ContractModel
from hwp_mcp_registry import (
    CapabilityCategory,
    CapabilityOperation,
    ExecutionPath,
    McpProfile,
    tool_specs,
)


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
    return NativeCapabilityInventory(
        native_protocol_required=9,
        capabilities=tuple(
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
        ),
    )
