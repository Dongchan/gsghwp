#Requires -Version 5.1

[CmdletBinding()]
param(
    [switch]$AcceptChanges
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$bootstrapRoot = Join-Path $PSScriptRoot "plugins\gsg-hwp"
$updateModule = Join-Path $bootstrapRoot "scripts\GsgHwp.Update.psm1"
Import-Module -Name $updateModule -Force

$localAppData = [Environment]::GetFolderPath("LocalApplicationData")
$updaterRoot = Join-Path $localAppData "GSG_HWP\updater"
$activePackageState = Join-Path $updaterRoot "active-package.json"
if (-not (Test-Path -LiteralPath $activePackageState -PathType Leaf)) {
    Write-Host "되돌릴 자동 업데이트 기록이 없습니다."
    exit 0
}

$state = Get-Content -LiteralPath $activePackageState -Raw -Encoding UTF8 |
    ConvertFrom-Json
$currentRoot = Get-GsgHwpActivePackageRoot -BootstrapRoot $bootstrapRoot `
    -LocalAppData $localAppData
if ($currentRoot -eq (Resolve-Path -LiteralPath $bootstrapRoot).Path) {
    throw "활성 자동 업데이트 패키지 상태가 유효하지 않습니다."
}
$installationModule = Join-Path $currentRoot "scripts\GsgHwp.Installation.psm1"
Import-Module -Name $installationModule -Force
$paths = Get-GsgHwpPaths -PackageRoot $currentRoot -LocalAppData $localAppData

Write-Host "GSG HWP 자동 업데이트 되돌리기 예정 변경사항"
Write-Host "  현재 배포 버전: $($state.distribution)"
Write-Host "  이전 패키지: $($state.previous_package_root)"
Write-Host "  직전 DLL과 HKCU 레지스트리 값 복원"
Write-Host "  업데이트 패키지와 감사용 백업은 보존"

if (-not $AcceptChanges) {
    Write-Host "변경사항을 확인한 뒤 -AcceptChanges 옵션으로 다시 실행하세요."
    exit 2
}
if (-not (Test-GsgHwpStopped)) {
    throw "DLL을 안전하게 되돌리려면 실행 중인 한/글을 모두 종료해야 합니다."
}

Restore-GsgHwpBackup -Paths $paths -BackupFile ([string]$state.rollback_backup_file)
if (
    $state.PSObject.Properties.Name -contains "previous_install_state_file" -and
    $null -ne $state.previous_install_state_file -and
    (Test-Path -LiteralPath ([string]$state.previous_install_state_file) -PathType Leaf)
) {
    Copy-Item -LiteralPath ([string]$state.previous_install_state_file) `
        -Destination $paths.ActiveState -Force
}
elseif (Test-Path -LiteralPath $paths.ActiveState -PathType Leaf) {
    Remove-Item -LiteralPath $paths.ActiveState -Force
}

if (
    $state.PSObject.Properties.Name -contains "previous_update_state_file" -and
    $null -ne $state.previous_update_state_file -and
    (Test-Path -LiteralPath ([string]$state.previous_update_state_file) -PathType Leaf)
) {
    Copy-Item -LiteralPath ([string]$state.previous_update_state_file) `
        -Destination $activePackageState -Force
}
else {
    Remove-Item -LiteralPath $activePackageState -Force
}

Write-Host "직전 GSG HWP 버전으로 복원했습니다."
Write-Host "한/글과 Codex·Claude를 다시 시작하세요."
