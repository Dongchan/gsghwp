#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-GsgHwpLauncherError {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Message + [Environment]::NewLine)
    $stream = [Console]::OpenStandardError()
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush()
}

$bootstrapRoot = Split-Path -Parent $PSScriptRoot
$localAppData = [Environment]::GetFolderPath("LocalApplicationData")
$bootstrapUpdateModule = Join-Path $bootstrapRoot "scripts\GsgHwp.Update.psm1"
if (-not (Test-Path -LiteralPath $bootstrapUpdateModule -PathType Leaf)) {
    [Console]::Error.WriteLine("GSG HWP automatic update bootstrap is missing.")
    Write-GsgHwpLauncherError -Message (
        "패키지에 scripts\GsgHwp.Update.psm1이 없습니다: $bootstrapUpdateModule"
    )
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

$pendingNativeMarker = Join-Path $localAppData "GSG_HWP\state\pending-native-install.json"
if (
    (-not (Test-Path -LiteralPath $python -PathType Leaf)) -or
    (Test-Path -LiteralPath $pendingNativeMarker -PathType Leaf)
) {
    $bootstrapModule = Join-Path $pluginRoot "scripts\GsgHwp.Bootstrap.psm1"
    if (-not (Test-Path -LiteralPath $bootstrapModule -PathType Leaf)) {
        $bootstrapModule = Join-Path $bootstrapRoot "scripts\GsgHwp.Bootstrap.psm1"
    }
    if (Test-Path -LiteralPath $bootstrapModule -PathType Leaf) {
        Import-Module -Name $bootstrapModule -Force
        $null = Initialize-GsgHwpRuntime -PackageRoot $pluginRoot `
            -PendingMarkerPath $pendingNativeMarker -LocalAppData $localAppData
    }
}

foreach ($requiredPath in @($launcher, $server)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        Write-GsgHwpLauncherError -Message (
            "GSG HWP 플러그인 파일이 빠져 있어 MCP 서버를 시작할 수 없습니다: $requiredPath"
        )
        Write-GsgHwpLauncherError -Message (
            "릴리스 자산 gsg-hwp-plugin-v<배포버전>.zip을 받아 zip 안의 gsg-hwp 폴더 내용을 " +
            "플러그인 폴더에 다시 풀어 넣은 뒤 MCP 클라이언트를 재시작하세요."
        )
        exit 2
    }
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-GsgHwpLauncherError -Message (
        "GSG HWP Python 런타임을 준비하지 못해 MCP 서버를 시작할 수 없습니다: $python"
    )
    Write-GsgHwpLauncherError -Message (
        "uv가 설치돼 있는지 확인하세요: winget install --id astral-sh.uv -e"
    )
    Write-GsgHwpLauncherError -Message (
        "자세한 기록: " + (Join-Path $localAppData "GSG_HWP\updater\logs")
    )
    exit 2
}

& $launcher $python "-B" $server
exit $LASTEXITCODE
