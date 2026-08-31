#Requires -Version 5.1

[CmdletBinding()]
param(
    [switch]$AcceptChanges
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$pluginRoot = Join-Path $PSScriptRoot "plugins\gsg-hwp"
$modulePath = Join-Path $pluginRoot "scripts\GsgHwp.Installation.psm1"
Import-Module -Name $modulePath -Force

$paths = Get-GsgHwpPaths -PackageRoot $pluginRoot
$manifest = Test-GsgHwpPackage -Paths $paths
$startMcp = Join-Path $pluginRoot "scripts\start-mcp.ps1"

$pythonVersionPath = Join-Path $pluginRoot ".python-version"
$pythonLabel = "Python"
if (Test-Path -LiteralPath $pythonVersionPath -PathType Leaf) {
    $pythonLabel = "Python " +
        (Get-Content -LiteralPath $pythonVersionPath -Raw -Encoding UTF8).Trim()
}

$updatePolicyPath = Join-Path $pluginRoot "update-policy.json"
$updateSummary = "자동 업데이트: update-policy.json을 읽지 못해 상태를 알 수 없습니다"
try {
    $updatePolicy = Get-Content -LiteralPath $updatePolicyPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if ([bool]$updatePolicy.enabled) {
        $updateSummary = "자동 업데이트: MCP 서버가 시작될 때 확인하되 직전 확인에서 " +
            "$([int]$updatePolicy.check_interval_hours)시간이 지났을 때만 GitHub Release를 " +
            "조회하고, 한/글이 모두 닫혀 있을 때만 해시 검증 뒤 적용합니다"
    }
    else {
        $updateSummary = "자동 업데이트: update-policy.json에서 꺼져 있습니다"
    }
}
catch {
    $null = $_
}

Write-Host "GSG HWP v$($manifest.distribution) 설치 예정 변경사항"
Write-Host "  네이티브 DLL 복사: $($paths.NativeDll)"
Write-Host "  파일 경로 보안 DLL 복사: $($paths.SecurityDll)"
Write-Host "  레지스트리: HKCU\Software\HNC\HwpUserAction\Modules -> 한컴브릿지"
Write-Host "  레지스트리: HKCU\Software\HNC\HwpUserAction\Modules\Uses -> 한컴브릿지"
Write-Host "  레지스트리: HKCU\Software\HNC\HwpAutomation\Modules -> FilePathCheckerModule"
Write-Host "  원본 백업: $($paths.BackupsRoot)"
Write-Host "  Python 환경: $($paths.RuntimeEnvironment)"
Write-Host "  $updateSummary"
Write-Host "  필요 도구: uv (없으면 winget install --id astral-sh.uv -e)"
Write-Host "관리자 권한과 HKLM 변경은 사용하지 않습니다."

if (-not $AcceptChanges) {
    Write-Host "변경사항을 확인한 뒤 -AcceptChanges 옵션으로 다시 실행하세요."
    exit 2
}

if (-not (Test-GsgHwpStopped)) {
    throw "DLL을 안전하게 설치하려면 실행 중인 한/글을 모두 종료해야 합니다."
}

$uv = Get-Command "uv.exe" -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    throw "uv가 필요합니다. 먼저 'winget install --id astral-sh.uv -e'를 실행하세요."
}

$previousEnvironment = $env:UV_PROJECT_ENVIRONMENT
try {
    $env:UV_PROJECT_ENVIRONMENT = $paths.RuntimeEnvironment
    & $uv.Source sync --managed-python --locked --no-dev --no-install-project `
        --project $pluginRoot
    if ($LASTEXITCODE -ne 0) {
        throw "잠금 파일에 맞춘 Python 런타임 설치에 실패했습니다."
    }
}
finally {
    if ($null -eq $previousEnvironment) {
        Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
    }
    else {
        $env:UV_PROJECT_ENVIRONMENT = $previousEnvironment
    }
}

$null = Test-GsgHwpSecurityModule -Paths $paths `
    -ExpectedSha256 $manifest.file_path_checker_sha256
$result = Install-GsgHwpNative -Paths $paths -PackageVersion $manifest.distribution
Write-Host "설치 완료"
Write-Host "  설치 전 상태 복구 백업: $($result.BackupFile)"
if ($result.RollbackBackupFile -ne $result.BackupFile) {
    Write-Host "  이번 회차 롤백 백업: $($result.RollbackBackupFile)"
}
Write-Host "  설치 네이티브 DLL: $($result.NativeDll)"
Write-Host "  설치 파일 경로 보안 DLL: $($result.SecurityDll)"
Write-Host "  전용 uv 관리 $pythonLabel 환경: $($paths.RuntimeEnvironment)"
Write-Host "  $updateSummary"
Write-Host "MCP 등록 명령(사용하는 앱에 맞게 하나만 실행):"
Write-Host "  Codex : codex plugin marketplace add innae1121-bit/gsghwp --ref main"
Write-Host "          codex plugin add gsg-hwp@gsg-hwp"
Write-Host ("  Claude: claude mcp add --transport stdio --scope user gsg-hwp-beta-live -- " +
    "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass " +
    "-File `"$startMcp`"")
Write-Host "한/글과 Codex·Claude를 다시 시작한 뒤 새 작업에서 플러그인을 사용하세요."
