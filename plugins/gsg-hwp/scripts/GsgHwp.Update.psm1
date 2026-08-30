Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:ReleasePrefix = "https://github.com/innae1121-bit/gsghwp/releases/"
$script:ReleaseDownloadPrefix = "$($script:ReleasePrefix)download/"

function Write-GsgHwpUpdateJson {
    param(
        [Parameter(Mandatory = $true)]
        $Value,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporaryPath = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
}

function Test-GsgHwpManagedChildPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Child,
        [Parameter(Mandatory = $true)]
        [string]$Parent
    )

    $resolvedChild = [System.IO.Path]::GetFullPath($Child)
    $resolvedParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd("\", "/") +
        [System.IO.Path]::DirectorySeparatorChar
    return $resolvedChild.StartsWith(
        $resolvedParent,
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Compare-GsgHwpVersion {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Left,
        [Parameter(Mandatory = $true)]
        [string]$Right
    )

    if ($Left -notmatch "^\d+\.\d+\.\d+$" -or $Right -notmatch "^\d+\.\d+\.\d+$") {
        throw "Stable update versions must use major.minor.patch."
    }
    return ([version]$Left).CompareTo([version]$Right)
}

function Read-GsgHwpUpdatePolicy {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $policy = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($property in @(
        "schema_version",
        "enabled",
        "channel",
        "manifest_url",
        "check_interval_hours",
        "repository"
    )) {
        if ($policy.PSObject.Properties.Name -notcontains $property) {
            throw "Update policy is missing '$property'."
        }
    }
    if ([int]$policy.schema_version -ne 1) {
        throw "Unsupported update policy schema."
    }
    if ([string]$policy.channel -ne "stable") {
        throw "Only the stable update channel is supported."
    }
    if (-not ([string]$policy.manifest_url).StartsWith(
        $script:ReleasePrefix,
        [StringComparison]::Ordinal
    )) {
        throw "Update manifest URL is not trusted."
    }
    $checkHours = [int]$policy.check_interval_hours
    if ($checkHours -lt 1 -or $checkHours -gt 168) {
        throw "Update check interval must be between 1 and 168 hours."
    }
    return $policy
}

function Test-GsgHwpUpdateManifest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        $Manifest
    )

    foreach ($property in @(
        "schema_version",
        "distribution",
        "source_version",
        "package_url",
        "package_sha256",
        "package_root",
        "native_bridge",
        "published_utc"
    )) {
        if ($Manifest.PSObject.Properties.Name -notcontains $property) {
            throw "Update manifest is missing '$property'."
        }
    }
    if ([int]$Manifest.schema_version -ne 1) {
        throw "Unsupported update manifest schema."
    }
    $distribution = [string]$Manifest.distribution
    $null = Compare-GsgHwpVersion -Left $distribution -Right $distribution
    $expectedUrl = (
        "$($script:ReleaseDownloadPrefix)v{0}/gsg-hwp-plugin-v{0}.zip" -f $distribution
    )
    if ([string]$Manifest.package_url -cne $expectedUrl) {
        throw "Update package URL does not match the trusted release asset."
    }
    if ([string]$Manifest.package_sha256 -notmatch "^[0-9a-fA-F]{64}$") {
        throw "Update package SHA-256 is invalid."
    }
    if ([string]$Manifest.package_root -cne "gsg-hwp") {
        throw "Update package root is invalid."
    }
    if ([string]$Manifest.native_bridge -notmatch "^\d+\.\d+\.\d+$") {
        throw "Native bridge version is invalid."
    }
    $null = [DateTimeOffset]::Parse([string]$Manifest.published_utc)
    return $true
}

function Expand-GsgHwpUpdateArchive {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ArchivePath,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $resolvedDestination = [System.IO.Path]::GetFullPath($Destination)
    $destinationPrefix = $resolvedDestination.TrimEnd("\", "/") +
        [System.IO.Path]::DirectorySeparatorChar
    New-Item -ItemType Directory -Path $resolvedDestination -Force | Out-Null
    $archive = [System.IO.Compression.ZipFile]::OpenRead(
        [System.IO.Path]::GetFullPath($ArchivePath)
    )
    try {
        foreach ($entry in $archive.Entries) {
            if (
                $entry.FullName.StartsWith("/") -or
                $entry.FullName.StartsWith("\") -or
                $entry.FullName -match "^[A-Za-z]:"
            ) {
                throw "Update archive contains an absolute path."
            }
            $relativePath = $entry.FullName.Replace(
                "/",
                [System.IO.Path]::DirectorySeparatorChar
            )
            $entryDestination = [System.IO.Path]::GetFullPath(
                (Join-Path $resolvedDestination $relativePath)
            )
            if (-not $entryDestination.StartsWith(
                $destinationPrefix,
                [StringComparison]::OrdinalIgnoreCase
            )) {
                throw "Update archive contains a path traversal entry."
            }
            if ([string]::IsNullOrEmpty($entry.Name)) {
                New-Item -ItemType Directory -Path $entryDestination -Force | Out-Null
                continue
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $entryDestination) `
                -Force | Out-Null
            $inputStream = $entry.Open()
            $outputStream = [System.IO.File]::Open(
                $entryDestination,
                [System.IO.FileMode]::Create,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None
            )
            try {
                $inputStream.CopyTo($outputStream)
            }
            finally {
                $outputStream.Dispose()
                $inputStream.Dispose()
            }
        }
    }
    finally {
        $archive.Dispose()
    }
}

function Get-GsgHwpActivePackageRoot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$BootstrapRoot,
        [string]$LocalAppData = [Environment]::GetFolderPath("LocalApplicationData")
    )

    $resolvedBootstrap = (Resolve-Path -LiteralPath $BootstrapRoot).Path
    $dataRoot = Join-Path $LocalAppData "GSG_HWP"
    $packagesRoot = Join-Path $dataRoot "packages"
    $statePath = Join-Path $dataRoot "updater\active-package.json"
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        return $resolvedBootstrap
    }
    try {
        $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $candidateRoot = [string]$state.package_root
        if (-not (Test-GsgHwpManagedChildPath -Child $candidateRoot -Parent $packagesRoot)) {
            return $resolvedBootstrap
        }
        $manifestPath = Join-Path $candidateRoot "compatibility-manifest.json"
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            return $resolvedBootstrap
        }
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        if ([string]$manifest.distribution -ne [string]$state.distribution) {
            return $resolvedBootstrap
        }
        return [System.IO.Path]::GetFullPath($candidateRoot)
    }
    catch {
        return $resolvedBootstrap
    }
}

function Test-GsgHwpUpdateCheckDue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StatePath,
        [Parameter(Mandatory = $true)]
        [int]$CheckIntervalHours
    )

    if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
        return $true
    }
    try {
        $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $checked = [DateTimeOffset]::Parse([string]$state.checked_utc)
        return [DateTimeOffset]::UtcNow -ge $checked.AddHours($CheckIntervalHours)
    }
    catch {
        return $true
    }
}

function Invoke-GsgHwpRuntimeSync {
    param(
        [Parameter(Mandatory = $true)]
        $Paths,
        [Parameter(Mandatory = $true)]
        [string]$PluginRoot,
        [Parameter(Mandatory = $true)]
        [string]$LogPath
    )

    $uv = Get-Command "uv.exe" -ErrorAction SilentlyContinue
    if ($null -eq $uv) {
        throw "uv is required before an automatic update can be activated."
    }
    $previousEnvironment = $env:UV_PROJECT_ENVIRONMENT
    try {
        $env:UV_PROJECT_ENVIRONMENT = $Paths.RuntimeEnvironment
        $ErrorActionPreference = "Continue"
        $syncOutput = & $uv.Source sync --managed-python --locked --no-dev `
            --no-install-project --project $PluginRoot 2>&1
        $ErrorActionPreference = "Stop"
        $syncOutput | Set-Content -LiteralPath $LogPath -Encoding UTF8
        if ($LASTEXITCODE -ne 0) {
            throw "Automatic update Python runtime synchronization failed."
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
}

function Test-GsgHwpUpdatedRuntime {
    param(
        [Parameter(Mandatory = $true)]
        $Paths,
        [Parameter(Mandatory = $true)]
        [string]$PluginRoot,
        [Parameter(Mandatory = $true)]
        [string]$LogPath
    )

    $python = Join-Path $Paths.RuntimeEnvironment "Scripts\python.exe"
    $scripts = Join-Path $PluginRoot "skills\automate-hancom-documents\scripts"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Updated Python runtime is missing."
    }
    $previousPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $scripts
        $ErrorActionPreference = "Continue"
        $testOutput = & $python -B -c (
            "import hwp_mcp; assert callable(hwp_mcp.build_server)"
        ) 2>&1
        $ErrorActionPreference = "Stop"
        $testOutput | Add-Content -LiteralPath $LogPath -Encoding UTF8
        if ($LASTEXITCODE -ne 0) {
            throw "Updated MCP runtime self-test failed."
        }
    }
    finally {
        if ($null -eq $previousPythonPath) {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        }
        else {
            $env:PYTHONPATH = $previousPythonPath
        }
    }
}

function Invoke-GsgHwpAutoUpdate {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$BootstrapRoot,
        [Parameter(Mandatory = $true)]
        [string]$CurrentRoot,
        [string]$LocalAppData = [Environment]::GetFolderPath("LocalApplicationData")
    )

    $policyPath = Join-Path $CurrentRoot "update-policy.json"
    if (-not (Test-Path -LiteralPath $policyPath -PathType Leaf)) {
        return $CurrentRoot
    }
    $policy = Read-GsgHwpUpdatePolicy -Path $policyPath
    if (-not [bool]$policy.enabled) {
        return $CurrentRoot
    }
    if ($env:GSG_HWP_AUTO_UPDATE -in @("0", "false", "off")) {
        return $CurrentRoot
    }

    $dataRoot = Join-Path $LocalAppData "GSG_HWP"
    $updaterRoot = Join-Path $dataRoot "updater"
    $checkStatePath = Join-Path $updaterRoot "last-check.json"
    if (-not (Test-GsgHwpUpdateCheckDue -StatePath $checkStatePath `
        -CheckIntervalHours ([int]$policy.check_interval_hours))) {
        return $CurrentRoot
    }

    $updateId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ") + "-" +
        [Guid]::NewGuid().ToString("N").Substring(0, 8)
    $stagingRoot = Join-Path $updaterRoot "staging\$updateId"
    $logsRoot = Join-Path $updaterRoot "logs"
    $logPath = Join-Path $logsRoot "$updateId.log"
    New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $logsRoot -Force | Out-Null

    $paths = $null
    $installResult = $null
    $previousInstallStateFile = $null
    $previousUpdateStateFile = $null
    try {
        $latestPath = Join-Path $stagingRoot "latest.json"
        $previousProgress = $ProgressPreference
        try {
            $ProgressPreference = "SilentlyContinue"
            Invoke-WebRequest -Uri ([string]$policy.manifest_url) -OutFile $latestPath `
                -UseBasicParsing
        }
        finally {
            $ProgressPreference = $previousProgress
        }
        $latest = Get-Content -LiteralPath $latestPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $null = Test-GsgHwpUpdateManifest -Manifest $latest
        Write-GsgHwpUpdateJson -Value ([pscustomobject][ordered]@{
            schema_version = 1
            checked_utc = [DateTimeOffset]::UtcNow.ToString("o")
            latest_distribution = [string]$latest.distribution
            status = "checked"
        }) -Path $checkStatePath

        $currentManifest = Get-Content -LiteralPath (
            Join-Path $CurrentRoot "compatibility-manifest.json"
        ) -Raw -Encoding UTF8 | ConvertFrom-Json
        if ((Compare-GsgHwpVersion -Left ([string]$latest.distribution) `
            -Right ([string]$currentManifest.distribution)) -le 0) {
            return $CurrentRoot
        }
        if (@(Get-Process -Name "Hwp" -ErrorAction SilentlyContinue).Count -gt 0) {
            Write-GsgHwpUpdateJson -Value ([pscustomobject][ordered]@{
                schema_version = 1
                distribution = [string]$latest.distribution
                detected_utc = [DateTimeOffset]::UtcNow.ToString("o")
                reason = "hwp_running"
            }) -Path (Join-Path $updaterRoot "pending-update.json")
            [Console]::Error.WriteLine(
                "GSG HWP v$($latest.distribution) 업데이트는 한/글 종료 후 자동 적용됩니다."
            )
            return $CurrentRoot
        }

        $archivePath = Join-Path $stagingRoot "package.zip"
        $previousProgress = $ProgressPreference
        try {
            $ProgressPreference = "SilentlyContinue"
            Invoke-WebRequest -Uri ([string]$latest.package_url) -OutFile $archivePath `
                -UseBasicParsing
        }
        finally {
            $ProgressPreference = $previousProgress
        }
        $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
        if ($archiveHash -ine [string]$latest.package_sha256) {
            throw "Automatic update package checksum verification failed."
        }

        $extractedRoot = Join-Path $stagingRoot "extracted"
        Expand-GsgHwpUpdateArchive -ArchivePath $archivePath -Destination $extractedRoot
        $candidateRoot = Join-Path $extractedRoot ([string]$latest.package_root)
        $candidateModule = Join-Path $candidateRoot "scripts\GsgHwp.Installation.psm1"
        if (-not (Test-Path -LiteralPath $candidateModule -PathType Leaf)) {
            throw "Automatic update package is missing the installation module."
        }
        Import-Module -Name $candidateModule -Force
        $candidatePaths = Get-GsgHwpPaths -PackageRoot $candidateRoot `
            -LocalAppData $LocalAppData
        $candidateManifest = Test-GsgHwpPackage -Paths $candidatePaths
        if ([string]$candidateManifest.distribution -ne [string]$latest.distribution) {
            throw "Automatic update package version does not match latest.json."
        }

        $packagesRoot = Join-Path $dataRoot "packages"
        $versionRoot = Join-Path $packagesRoot ([string]$latest.distribution)
        $finalRoot = Join-Path $versionRoot "gsg-hwp"
        if (Test-Path -LiteralPath $versionRoot) {
            if (-not (Test-Path -LiteralPath $finalRoot -PathType Container)) {
                throw "Managed update version directory already exists but is incomplete."
            }
        }
        else {
            New-Item -ItemType Directory -Path $versionRoot -Force | Out-Null
            Move-Item -LiteralPath $candidateRoot -Destination $finalRoot
        }

        $finalModule = Join-Path $finalRoot "scripts\GsgHwp.Installation.psm1"
        Import-Module -Name $finalModule -Force
        $paths = Get-GsgHwpPaths -PackageRoot $finalRoot -LocalAppData $LocalAppData
        $finalManifest = Test-GsgHwpPackage -Paths $paths
        if ([string]$finalManifest.distribution -ne [string]$latest.distribution) {
            throw "Managed update package validation failed."
        }
        Invoke-GsgHwpRuntimeSync -Paths $paths -PluginRoot $finalRoot -LogPath $logPath
        Test-GsgHwpUpdatedRuntime -Paths $paths -PluginRoot $finalRoot -LogPath $logPath
        $null = Test-GsgHwpSecurityModule -Paths $paths `
            -ExpectedSha256 ([string]$finalManifest.file_path_checker_sha256)

        $activeUpdaterStatePath = Join-Path $updaterRoot "active-package.json"
        if (
            (Test-Path -LiteralPath $paths.ActiveState -PathType Leaf) -or
            (Test-Path -LiteralPath $activeUpdaterStatePath -PathType Leaf)
        ) {
            $installStateBackupRoot = Join-Path $paths.BackupsRoot "update-state-$updateId"
            New-Item -ItemType Directory -Path $installStateBackupRoot -Force | Out-Null
            if (Test-Path -LiteralPath $paths.ActiveState -PathType Leaf) {
                $previousInstallStateFile = Join-Path $installStateBackupRoot "active-install.json"
                Copy-Item -LiteralPath $paths.ActiveState -Destination $previousInstallStateFile
            }
            if (Test-Path -LiteralPath $activeUpdaterStatePath -PathType Leaf) {
                $previousUpdateStateFile = Join-Path $installStateBackupRoot "active-package.json"
                Copy-Item -LiteralPath $activeUpdaterStatePath `
                    -Destination $previousUpdateStateFile
            }
        }

        $installResult = Install-GsgHwpNative -Paths $paths `
            -PackageVersion ([string]$finalManifest.distribution)
        $activeStatePath = Join-Path $updaterRoot "active-package.json"
        Write-GsgHwpUpdateJson -Value ([pscustomobject][ordered]@{
            schema_version = 1
            distribution = [string]$finalManifest.distribution
            package_root = $finalRoot
            previous_package_root = $CurrentRoot
            rollback_backup_file = [string]$installResult.RollbackBackupFile
            previous_install_state_file = $previousInstallStateFile
            previous_update_state_file = $previousUpdateStateFile
            activated_utc = [DateTimeOffset]::UtcNow.ToString("o")
        }) -Path $activeStatePath
        $pendingPath = Join-Path $updaterRoot "pending-update.json"
        if (Test-Path -LiteralPath $pendingPath -PathType Leaf) {
            Remove-Item -LiteralPath $pendingPath -Force
        }
        [Console]::Error.WriteLine(
            "GSG HWP가 v$($finalManifest.distribution)로 자동 업데이트되었습니다."
        )
        return $finalRoot
    }
    catch {
        $activeUpdaterStatePath = Join-Path $updaterRoot "active-package.json"
        if ($null -ne $installResult -and $null -ne $paths) {
            Restore-GsgHwpBackup -Paths $paths `
                -BackupFile ([string]$installResult.RollbackBackupFile)
            if ($null -ne $previousInstallStateFile) {
                Copy-Item -LiteralPath $previousInstallStateFile `
                    -Destination $paths.ActiveState -Force
            }
            elseif (Test-Path -LiteralPath $paths.ActiveState -PathType Leaf) {
                Remove-Item -LiteralPath $paths.ActiveState -Force
            }
        }
        if ($null -ne $previousUpdateStateFile) {
            Copy-Item -LiteralPath $previousUpdateStateFile `
                -Destination $activeUpdaterStatePath -Force
        }
        elseif (Test-Path -LiteralPath $activeUpdaterStatePath -PathType Leaf) {
            Remove-Item -LiteralPath $activeUpdaterStatePath -Force
        }
        Write-GsgHwpUpdateJson -Value ([pscustomobject][ordered]@{
            schema_version = 1
            checked_utc = [DateTimeOffset]::UtcNow.ToString("o")
            status = "failed"
            error = $_.Exception.Message
        }) -Path $checkStatePath
        throw
    }
    finally {
        if (
            (Test-Path -LiteralPath $stagingRoot -PathType Container) -and
            (Test-GsgHwpManagedChildPath -Child $stagingRoot -Parent $updaterRoot)
        ) {
            Remove-Item -LiteralPath $stagingRoot -Recurse -Force
        }
    }
}

function Resolve-GsgHwpPackageRoot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$BootstrapRoot,
        [string]$LocalAppData = [Environment]::GetFolderPath("LocalApplicationData")
    )

    $currentRoot = Get-GsgHwpActivePackageRoot -BootstrapRoot $BootstrapRoot `
        -LocalAppData $LocalAppData
    try {
        return Invoke-GsgHwpAutoUpdate -BootstrapRoot $BootstrapRoot `
            -CurrentRoot $currentRoot -LocalAppData $LocalAppData
    }
    catch {
        [Console]::Error.WriteLine(
            "GSG HWP 자동 업데이트를 적용하지 못해 현재 버전을 실행합니다: " +
            $_.Exception.Message
        )
        return $currentRoot
    }
}

Export-ModuleMember -Function @(
    "Compare-GsgHwpVersion",
    "Expand-GsgHwpUpdateArchive",
    "Get-GsgHwpActivePackageRoot",
    "Invoke-GsgHwpAutoUpdate",
    "Invoke-GsgHwpRuntimeSync",
    "Read-GsgHwpUpdatePolicy",
    "Resolve-GsgHwpPackageRoot",
    "Test-GsgHwpUpdatedRuntime",
    "Test-GsgHwpUpdateManifest"
)
