#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$bootstrapRoot = Split-Path -Parent $PSScriptRoot
$localAppData = [Environment]::GetFolderPath("LocalApplicationData")
$bootstrapUpdateModule = Join-Path $bootstrapRoot "scripts\GsgHwp.Update.psm1"
if (-not (Test-Path -LiteralPath $bootstrapUpdateModule -PathType Leaf)) {
    [Console]::Error.WriteLine("GSG HWP automatic update bootstrap is missing.")
    exit 2
}

Import-Module -Name $bootstrapUpdateModule -Force
$activeRoot = Get-GsgHwpActivePackageRoot -BootstrapRoot $bootstrapRoot `
    -LocalAppData $localAppData
$activeUpdateModule = Join-Path $activeRoot "scripts\GsgHwp.Update.psm1"
if (
    $activeRoot -ne $bootstrapRoot -and
    (Test-Path -LiteralPath $activeUpdateModule -PathType Leaf)
) {
    Import-Module -Name $activeUpdateModule -Force
}
$pluginRoot = Resolve-GsgHwpPackageRoot -BootstrapRoot $bootstrapRoot `
    -LocalAppData $localAppData
$manifest = Get-Content -LiteralPath (Join-Path $pluginRoot "compatibility-manifest.json") -Raw -Encoding UTF8 |
    ConvertFrom-Json
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
