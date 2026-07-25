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

Write-Host "GSG HWP v$($manifest.distribution) 원상복구 예정 변경사항"
Write-Host "  설치 전 HKCU 레지스트리 3개 값 복원"
Write-Host "  설치 전 네이티브/파일 경로 보안 DLL 복원 또는 GSG HWP가 추가한 DLL 제거"
if (-not $KeepRuntime) {
    Write-Host "  전용 Python 환경 제거: $($paths.RuntimeVersionRoot)"
}
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
Write-Host "Codex 플러그인 제거 명령: codex plugin remove gsg-hwp@gsg-hwp"
Write-Host "변경 사항 적용을 위해 한/글과 Codex를 다시 시작하세요."
