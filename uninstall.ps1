#Requires -Version 5.1

[CmdletBinding()]
param(
    [switch]$AcceptChanges,
    [switch]$KeepRuntime
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$pluginRoot = Join-Path $PSScriptRoot "plugins\gsg-hwp"
$modulePath = Join-Path $pluginRoot "scripts\GsgHwp.Installation.psm1"
Import-Module -Name $modulePath -Force

$paths = Get-GsgHwpPaths -PackageRoot $pluginRoot
$manifest = Get-Content -LiteralPath $paths.ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

$installedVersion = [string]$manifest.distribution
try {
    if (Test-Path -LiteralPath $paths.ActiveState -PathType Leaf) {
        $activeState = Get-Content -LiteralPath $paths.ActiveState -Raw -Encoding UTF8 |
            ConvertFrom-Json
        if (-not [string]::IsNullOrWhiteSpace([string]$activeState.package_version)) {
            $installedVersion = [string]$activeState.package_version
        }
    }
}
catch {
    $null = $_
}

Write-Host "GSG HWP v$installedVersion 원상복구 예정 변경사항"
Write-Host "  먼저 MCP·플러그인 등록을 지우세요. 남아 있으면 다음 클라이언트 실행이"
Write-Host "  런타임과 네이티브 DLL·HKCU 값을 자동으로 다시 설치합니다."
Write-Host "  설치 전 HKCU 레지스트리 3개 값 복원"
Write-Host "  설치 전 네이티브/파일 경로 보안 DLL 복원 또는 GSG HWP가 추가한 DLL 제거"
Write-Host ("  자동 업데이트 패키지·상태와 준비/업데이트 기록 제거: " +
    "$($paths.PackagesRoot), $($paths.UpdaterRoot)")
if (-not $KeepRuntime) {
    Write-Host "  전용 Python 환경 제거(설치된 모든 버전): $($paths.RuntimeRoot)"
}
Write-Host "  제거하지 않는 항목: $($paths.StateRoot)의 준비 마커·잠금 파일"
Write-Host "  복구 백업 파일은 감사와 추가 복구를 위해 보존"

if (-not $AcceptChanges) {
    Write-Host "변경사항을 확인한 뒤 -AcceptChanges 옵션으로 다시 실행하세요."
    exit 2
}

if (-not (Test-GsgHwpStopped)) {
    throw "DLL을 안전하게 복구하려면 실행 중인 한/글을 모두 종료해야 합니다."
}

$result = Restore-GsgHwpNative -Paths $paths
Remove-GsgHwpManagedUpdates -Paths $paths -KeepRuntime:$KeepRuntime

if ($result.Restored) {
    Write-Host "원상복구 완료"
    Write-Host "  사용한 백업: $($result.BackupFile)"
}
else {
    Write-Host "활성 설치 기록이 없어 DLL과 레지스트리는 변경하지 않았습니다."
}
Write-Host "MCP·플러그인 등록 제거 명령(이 스크립트보다 먼저 실행해야 재설치를 막습니다):"
Write-Host "  codex plugin remove gsg-hwp@gsg-hwp"
Write-Host "  codex plugin marketplace remove gsg-hwp"
Write-Host "  claude mcp remove gsg-hwp-beta-live"
Write-Host "변경 사항 적용을 위해 한/글과 Codex·Claude를 다시 시작하세요."
