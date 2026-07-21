#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$pluginRoot = Split-Path -Parent $PSScriptRoot
$manifest = Get-Content -LiteralPath (Join-Path $pluginRoot "compatibility-manifest.json") -Raw -Encoding UTF8 |
    ConvertFrom-Json
$localAppData = [Environment]::GetFolderPath("LocalApplicationData")
$python = Join-Path $localAppData (
    "GSG_HWP\runtime\{0}\.venv\Scripts\python.exe" -f $manifest.distribution
)
$launcher = Join-Path $pluginRoot (
    "addon\HancomMcpLauncher\bin\Release\HancomMcpLauncher.exe"
)
$server = Join-Path $pluginRoot (
    "skills\automate-hancom-documents\scripts\hwp_mcp_hot_reload.py"
)

foreach ($requiredPath in @($python, $launcher, $server)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        [Console]::Error.WriteLine(
            "GSG HWP runtime is incomplete. Clone the repository and run install.ps1 -AcceptChanges."
        )
        exit 2
    }
}

& $launcher $python "-B" $server
exit $LASTEXITCODE
