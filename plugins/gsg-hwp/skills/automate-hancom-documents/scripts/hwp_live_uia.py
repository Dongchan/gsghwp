from __future__ import annotations

import base64
import json
import os
import subprocess
from dataclasses import dataclass
from typing import Final, Protocol, final

from pydantic import Field, TypeAdapter

from hwp_errors import HwpLiveError
from hwp_live_bridge_contract import HancomDialogControlState
from hwp_live_values import ContractModel


_CREATE_NO_WINDOW: Final = 0x0800_0000
_POWERSHELL_TIMEOUT_SECONDS: Final = 5
_CONTROL_PAYLOADS = TypeAdapter(list["_UiAutomationControlPayload"])

_INSPECT_SCRIPT: Final = r"""
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
Add-Type -AssemblyName UIAutomationClient
$root = [System.Windows.Automation.AutomationElement]::FromHandle(
  [IntPtr][Int64]$env:HWP_UIA_DIALOG_HANDLE
)
$all = $root.FindAll(
  [System.Windows.Automation.TreeScope]::Descendants,
  [System.Windows.Automation.Condition]::TrueCondition
)
$items = @()
for ($index = 0; $index -lt $all.Count; $index++) {
  $element = $all.Item($index)
  $pattern = $null
  $supportsInvoke = $element.TryGetCurrentPattern(
    [System.Windows.Automation.InvokePattern]::Pattern,
    [ref]$pattern
  )
  $controlType = $element.Current.ControlType.ProgrammaticName
  if ($controlType.StartsWith('ControlType.')) {
    $controlType = $controlType.Substring(12)
  }
  $items += [pscustomobject]@{
    control_id = $index
    title = $element.Current.Name
    class_name = $element.Current.ClassName
    automation_id = $element.Current.AutomationId
    control_type = $controlType
    visible = -not $element.Current.IsOffscreen
    enabled = $element.Current.IsEnabled
    focused = $element.Current.HasKeyboardFocus
    accelerator = $element.Current.AcceleratorKey
    actionable = $supportsInvoke -and $element.Current.IsEnabled -and
      (-not $element.Current.IsOffscreen)
  }
}
ConvertTo-Json -InputObject @($items) -Compress
"""

_INVOKE_SCRIPT: Final = r"""
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
Add-Type -AssemblyName UIAutomationClient
$expectedJson = [Text.Encoding]::UTF8.GetString(
  [Convert]::FromBase64String($env:HWP_UIA_EXPECTED)
)
$expected = $expectedJson | ConvertFrom-Json
$root = [System.Windows.Automation.AutomationElement]::FromHandle(
  [IntPtr][Int64]$env:HWP_UIA_DIALOG_HANDLE
)
$all = $root.FindAll(
  [System.Windows.Automation.TreeScope]::Descendants,
  [System.Windows.Automation.Condition]::TrueCondition
)
if ($expected.control_id -lt 0 -or $expected.control_id -ge $all.Count) {
  throw 'selected UI Automation control no longer exists'
}
$element = $all.Item([int]$expected.control_id)
$controlType = $element.Current.ControlType.ProgrammaticName
if ($controlType.StartsWith('ControlType.')) {
  $controlType = $controlType.Substring(12)
}
if (
  $element.Current.Name -ne $expected.title -or
  $element.Current.ClassName -ne $expected.class_name -or
  $element.Current.AutomationId -ne $expected.automation_id -or
  $controlType -ne $expected.control_type -or
  -not $element.Current.IsEnabled -or
  $element.Current.IsOffscreen
) {
  throw 'selected UI Automation control changed before invocation'
}
$pattern = $null
if (-not $element.TryGetCurrentPattern(
  [System.Windows.Automation.InvokePattern]::Pattern,
  [ref]$pattern
)) {
  throw 'selected UI Automation control does not support InvokePattern'
}
$pattern.Invoke()
[pscustomobject]@{ delivered = $true } | ConvertTo-Json -Compress
"""


class _UiAutomationControlPayload(ContractModel):
    control_id: int = Field(ge=0, le=65_535)
    title: str = Field(max_length=4_000)
    class_name: str = Field(max_length=200)
    automation_id: str = Field(max_length=500)
    control_type: str = Field(max_length=100)
    visible: bool
    enabled: bool
    focused: bool
    accelerator: str = Field(max_length=100)
    actionable: bool


class _UiAutomationInvokePayload(ContractModel):
    delivered: bool


@final
@dataclass(frozen=True, slots=True)
class UiAutomationControlState:
    control_id: int
    title: str
    class_name: str
    automation_id: str
    control_type: str
    visible: bool
    enabled: bool
    focused: bool
    accelerator: str | None
    actionable: bool


class UiAutomationReader(Protocol):
    def inspect(
        self,
        dialog_window_handle: int,
    ) -> tuple[UiAutomationControlState, ...]: ...

    def invoke(
        self,
        dialog_window_handle: int,
        selected: HancomDialogControlState,
    ) -> bool: ...


def _run_powershell(
    script: str,
    dialog_window_handle: int,
    *,
    environment: dict[str, str] | None = None,
) -> str:
    resolved_environment = (
        os.environ.copy() if environment is None else dict(environment)
    )
    resolved_environment["HWP_UIA_DIALOG_HANDLE"] = str(dialog_window_handle)
    try:
        completed = subprocess.run(
            (
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_POWERSHELL_TIMEOUT_SECONDS,
            creationflags=_CREATE_NO_WINDOW,
            env=resolved_environment,
        )
    except subprocess.CalledProcessError as error:
        raise HwpLiveError(
            f"한컴 WPF 팝업 UI Automation 호출에 실패했습니다: {error}"
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise HwpLiveError("한컴 WPF 팝업 UI Automation 호출에 실패했습니다") from error
    return completed.stdout.strip()


@final
class PowerShellUiAutomationReader:
    def inspect(
        self,
        dialog_window_handle: int,
    ) -> tuple[UiAutomationControlState, ...]:
        payloads = _CONTROL_PAYLOADS.validate_json(
            _run_powershell(_INSPECT_SCRIPT, dialog_window_handle)
        )
        return tuple(
            UiAutomationControlState(
                control_id=item.control_id,
                title=item.title,
                class_name=item.class_name,
                automation_id=item.automation_id,
                control_type=item.control_type,
                visible=item.visible,
                enabled=item.enabled,
                focused=item.focused,
                accelerator=item.accelerator or None,
                actionable=item.actionable,
            )
            for item in payloads
        )

    def invoke(
        self,
        dialog_window_handle: int,
        selected: HancomDialogControlState,
    ) -> bool:
        expected = {
            "control_id": selected.control_id,
            "title": selected.title,
            "class_name": selected.class_name,
            "automation_id": selected.automation_id or "",
            "control_type": selected.control_type or "",
        }
        environment = os.environ.copy()
        environment["HWP_UIA_EXPECTED"] = base64.b64encode(
            json.dumps(
                expected,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).decode("ascii")
        response = _UiAutomationInvokePayload.model_validate_json(
            _run_powershell(
                _INVOKE_SCRIPT,
                dialog_window_handle,
                environment=environment,
            )
        )
        return response.delivered
