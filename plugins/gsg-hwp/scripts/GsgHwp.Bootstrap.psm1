Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:StatusPrefix = "[GSG HWP] "
$script:ErrorStream = $null

function Write-GsgHwpBootstrapStatus {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if ($null -eq $script:ErrorStream) {
        $script:ErrorStream = [Console]::OpenStandardError()
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(
        $script:StatusPrefix + $Message + [Environment]::NewLine
    )
    $script:ErrorStream.Write($bytes, 0, $bytes.Length)
    $script:ErrorStream.Flush()
}

function Open-GsgHwpBootstrapLock {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$LockPath,
        [int]$TimeoutSeconds = 1800,
        [int]$StaleMinutes = 60
    )

    New-Item -ItemType Directory -Path (Split-Path -Parent $LockPath) -Force | Out-Null
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $announced = $false
    while ($true) {
        try {
            $stream = [System.IO.File]::Open(
                $LockPath,
                [System.IO.FileMode]::Create,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
            $owner = [System.Text.Encoding]::UTF8.GetBytes(
                ("pid={0} acquired_utc={1}" -f $PID, [DateTime]::UtcNow.ToString("o"))
            )
            $stream.Write($owner, 0, $owner.Length)
            $stream.Flush()
            return $stream
        }
        catch [System.IO.IOException] {
            $lastWriteUtc = $null
            try {
                $lastWriteUtc = (Get-Item -LiteralPath $LockPath -Force).LastWriteTimeUtc
            }
            catch {
                $lastWriteUtc = $null
            }
            if (
                $null -ne $lastWriteUtc -and
                $lastWriteUtc -lt [DateTime]::UtcNow.AddMinutes(-$StaleMinutes)
            ) {
                Write-GsgHwpBootstrapStatus -Message (
                    "오래된 준비 잠금을 무시하고 계속합니다: $LockPath"
                )
                return $null
            }
            if (-not $announced) {
                Write-GsgHwpBootstrapStatus -Message (
                    "다른 창에서 GSG HWP 런타임을 준비하고 있습니다. " +
                    "최대 $([int]($TimeoutSeconds / 60))분까지 기다립니다."
                )
                $announced = $true
            }
            if ([DateTime]::UtcNow -ge $deadline) {
                Write-GsgHwpBootstrapStatus -Message (
                    "준비 잠금을 $([int]($TimeoutSeconds / 60))분 기다렸지만 풀리지 않아 " +
                    "잠금 없이 계속합니다."
                )
                return $null
            }
            Start-Sleep -Milliseconds 500
        }
        catch {
            Write-GsgHwpBootstrapStatus -Message (
                "준비 잠금을 열지 못해 잠금 없이 계속합니다: " + $_.Exception.Message
            )
            return $null
        }
    }
}

function Close-GsgHwpBootstrapLock {
    param(
        $Lock
    )

    if ($null -eq $Lock) {
        return
    }
    try {
        $Lock.Dispose()
    }
    catch {
        $null = $_
    }
}

function Get-GsgHwpBootstrapState {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        $Paths,
        [Parameter(Mandatory = $true)]
        [string]$PendingMarkerPath
    )

    $python = Join-Path $Paths.RuntimeEnvironment "Scripts\python.exe"
    return [pscustomobject][ordered]@{
        Python = $python
        NeedsRuntime = -not (Test-Path -LiteralPath $python -PathType Leaf)
        NeedsNative = (
            (Test-Path -LiteralPath $PendingMarkerPath -PathType Leaf) -or
            (-not (Test-Path -LiteralPath $Paths.NativeDll -PathType Leaf)) -or
            (-not (Test-Path -LiteralPath $Paths.SecurityDll -PathType Leaf))
        )
    }
}

function Set-GsgHwpPendingNativeMarker {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Distribution,
        [Parameter(Mandatory = $true)]
        [string]$Reason
    )

    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    $temporaryPath = "$Path.tmp"
    [pscustomobject][ordered]@{
        schema_version = 1
        distribution = $Distribution
        reason = $Reason
        detected_utc = [DateTimeOffset]::UtcNow.ToString("o")
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
}

function Remove-GsgHwpPendingNativeMarker {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Remove-Item -LiteralPath $Path -Force
    }
}

function Initialize-GsgHwpRuntime {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$PackageRoot,
        [Parameter(Mandatory = $true)]
        [string]$PendingMarkerPath,
        [string]$LocalAppData = [Environment]::GetFolderPath("LocalApplicationData"),
        [string]$ModulesKeyPath,
        [string]$AutomationModulesKeyPath,
        [int]$LockTimeoutSeconds = 1800,
        [int]$LockStaleMinutes = 60
    )

    $installationModule = Join-Path $PackageRoot "scripts\GsgHwp.Installation.psm1"
    $updateModule = Join-Path $PackageRoot "scripts\GsgHwp.Update.psm1"
    foreach ($requiredModule in @($installationModule, $updateModule)) {
        if (-not (Test-Path -LiteralPath $requiredModule -PathType Leaf)) {
            Write-GsgHwpBootstrapStatus -Message (
                "설치 모듈을 찾지 못해 런타임을 준비할 수 없습니다: $requiredModule"
            )
            return $false
        }
    }
    Import-Module -Name $installationModule -Force
    Import-Module -Name $updateModule -Force

    $paths = Get-GsgHwpPaths -PackageRoot $PackageRoot -LocalAppData $LocalAppData
    $state = Get-GsgHwpBootstrapState -Paths $paths -PendingMarkerPath $PendingMarkerPath
    if (-not $state.NeedsRuntime -and -not $state.NeedsNative) {
        return $true
    }

    $lock = Open-GsgHwpBootstrapLock `
        -LockPath (Join-Path $paths.StateRoot "runtime-bootstrap.lock") `
        -TimeoutSeconds $LockTimeoutSeconds -StaleMinutes $LockStaleMinutes
    try {
        $state = Get-GsgHwpBootstrapState -Paths $paths -PendingMarkerPath $PendingMarkerPath
        if (-not $state.NeedsRuntime -and -not $state.NeedsNative) {
            return $true
        }

        $manifest = Get-Content -LiteralPath $paths.ManifestPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $distribution = [string]$manifest.distribution
        $bootstrapId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ") + "-" +
            [Guid]::NewGuid().ToString("N").Substring(0, 8)
        $logsRoot = Join-Path $paths.UpdaterRoot "logs"
        New-Item -ItemType Directory -Path $logsRoot -Force | Out-Null
        $logPath = Join-Path $logsRoot "bootstrap-$bootstrapId.log"

        Write-GsgHwpBootstrapStatus -Message (
            "GSG HWP v$distribution 실행 준비를 시작합니다."
        )

        if ($state.NeedsRuntime) {
            $uv = Get-Command "uv.exe" -ErrorAction SilentlyContinue
            if ($null -eq $uv) {
                Write-GsgHwpBootstrapStatus -Message (
                    "GSG HWP v$distribution 실행에 필요한 Python 런타임이 없습니다: " +
                    $paths.RuntimeEnvironment
                )
                Write-GsgHwpBootstrapStatus -Message (
                    "런타임을 자동으로 만들려면 uv가 필요한데 uv.exe를 찾지 못했습니다."
                )
                Write-GsgHwpBootstrapStatus -Message (
                    "1) PowerShell에서 다음 명령으로 uv를 설치하세요: " +
                    "winget install --id astral-sh.uv -e"
                )
                Write-GsgHwpBootstrapStatus -Message (
                    "2) 설치한 뒤 MCP 클라이언트를 다시 시작하면 " +
                    "GSG HWP가 런타임을 스스로 갖춥니다."
                )
                Write-GsgHwpBootstrapStatus -Message (
                    "   최초 1회는 Python과 패키지를 내려받느라 몇 분 걸릴 수 있습니다."
                )
                return $false
            }

            Write-GsgHwpBootstrapStatus -Message (
                "Python 런타임을 구성합니다. 최초 1회는 몇 분 걸릴 수 있습니다: " +
                $paths.RuntimeEnvironment + " (기록: $logPath)"
            )
            try {
                Invoke-GsgHwpRuntimeSync -Paths $paths -PluginRoot $PackageRoot -LogPath $logPath
                Test-GsgHwpUpdatedRuntime -Paths $paths -PluginRoot $PackageRoot -LogPath $logPath
            }
            catch {
                Write-GsgHwpBootstrapStatus -Message (
                    "Python 런타임 구성에 실패했습니다: " + $_.Exception.Message
                )
                Write-GsgHwpBootstrapStatus -Message "자세한 기록: $logPath"
                try {
                    Remove-GsgHwpRuntime -Paths $paths
                    Write-GsgHwpBootstrapStatus -Message (
                        "다음 실행에서 다시 시도하도록 만들다 만 런타임을 지웠습니다."
                    )
                }
                catch {
                    Write-GsgHwpBootstrapStatus -Message (
                        "만들다 만 런타임을 지우지 못했습니다: " + $_.Exception.Message
                    )
                }
                return $false
            }
            Write-GsgHwpBootstrapStatus -Message "Python 런타임 준비 완료"
        }

        $state = Get-GsgHwpBootstrapState -Paths $paths -PendingMarkerPath $PendingMarkerPath
        if ($state.NeedsNative) {
            try {
                Write-GsgHwpBootstrapStatus -Message (
                    "패키지 무결성과 파일 경로 보안 모듈을 확인합니다."
                )
                $packageManifest = Test-GsgHwpPackage -Paths $paths
                $null = Test-GsgHwpSecurityModule -Paths $paths `
                    -ExpectedSha256 ([string]$packageManifest.file_path_checker_sha256)

                if (-not (Test-GsgHwpStopped)) {
                    Set-GsgHwpPendingNativeMarker -Path $PendingMarkerPath `
                        -Distribution $distribution -Reason "hwp_running"
                    Write-GsgHwpBootstrapStatus -Message (
                        "한/글이 실행 중이라 네이티브 브리지 설치를 건너뜁니다. " +
                        "한/글을 모두 닫은 뒤 MCP 클라이언트를 다시 시작하면 설치되고, " +
                        "그 다음 한/글을 실행하면 새 브리지가 물립니다."
                    )
                }
                else {
                    Write-GsgHwpBootstrapStatus -Message (
                        "네이티브 브리지를 설치하고 등록합니다."
                    )
                    $installArguments = @{
                        Paths = $paths
                        PackageVersion = [string]$packageManifest.distribution
                    }
                    if (-not [string]::IsNullOrWhiteSpace($ModulesKeyPath)) {
                        $installArguments["ModulesKeyPath"] = $ModulesKeyPath
                    }
                    if (-not [string]::IsNullOrWhiteSpace($AutomationModulesKeyPath)) {
                        $installArguments["AutomationModulesKeyPath"] = $AutomationModulesKeyPath
                    }
                    $null = Install-GsgHwpNative @installArguments
                    Remove-GsgHwpPendingNativeMarker -Path $PendingMarkerPath
                    Write-GsgHwpBootstrapStatus -Message "네이티브 브리지 준비 완료"
                }
            }
            catch {
                Set-GsgHwpPendingNativeMarker -Path $PendingMarkerPath `
                    -Distribution $distribution -Reason "install_failed"
                Write-GsgHwpBootstrapStatus -Message (
                    "네이티브 브리지 설치에 실패해 건너뜁니다: " + $_.Exception.Message
                )
            }
        }

        $state = Get-GsgHwpBootstrapState -Paths $paths -PendingMarkerPath $PendingMarkerPath
        if (-not $state.NeedsRuntime) {
            if ($state.NeedsNative) {
                Write-GsgHwpBootstrapStatus -Message (
                    "런타임은 준비했고 네이티브 브리지 설치만 남았습니다."
                )
            }
            else {
                Write-GsgHwpBootstrapStatus -Message "GSG HWP 실행 준비를 마쳤습니다."
            }
        }
        return (-not $state.NeedsRuntime)
    }
    finally {
        Close-GsgHwpBootstrapLock -Lock $lock
    }
}

Export-ModuleMember -Function @(
    "Get-GsgHwpBootstrapState",
    "Initialize-GsgHwpRuntime",
    "Open-GsgHwpBootstrapLock"
)
