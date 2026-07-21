Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:ModuleName = "한컴브릿지"
$script:DefaultModulesKey = "Software\HNC\HwpUserAction\Modules"

function Write-GsgHwpJson {
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

function Assert-GsgHwpChildPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Child,
        [Parameter(Mandatory = $true)]
        [string]$Parent
    )

    $resolvedChild = [System.IO.Path]::GetFullPath($Child)
    $resolvedParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd("\", "/") +
        [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedChild.StartsWith($resolvedParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to change a path outside the GSG HWP data directory: $resolvedChild"
    }
}

function Get-GsgHwpRegistrySnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$KeyPath,
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($KeyPath, $false)
    if ($null -eq $key) {
        return [pscustomobject][ordered]@{
            key_path = $KeyPath
            name = $Name
            key_existed = $false
            value_existed = $false
            kind = $null
            value = $null
        }
    }

    try {
        $valueExisted = $key.GetValueNames() -contains $Name
        if (-not $valueExisted) {
            return [pscustomobject][ordered]@{
                key_path = $KeyPath
                name = $Name
                key_existed = $true
                value_existed = $false
                kind = $null
                value = $null
            }
        }

        $kind = $key.GetValueKind($Name)
        $value = $key.GetValue(
            $Name,
            $null,
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
        )
        if ($kind -in @(
            [Microsoft.Win32.RegistryValueKind]::Binary,
            [Microsoft.Win32.RegistryValueKind]::None
        )) {
            $value = [Convert]::ToBase64String([byte[]]$value)
        }
        return [pscustomobject][ordered]@{
            key_path = $KeyPath
            name = $Name
            key_existed = $true
            value_existed = $true
            kind = $kind.ToString()
            value = $value
        }
    }
    finally {
        $key.Dispose()
    }
}

function Restore-GsgHwpRegistrySnapshot {
    param(
        [Parameter(Mandatory = $true)]
        $Snapshot
    )

    if ($Snapshot.value_existed) {
        $kind = [Microsoft.Win32.RegistryValueKind][Enum]::Parse(
            [Microsoft.Win32.RegistryValueKind],
            [string]$Snapshot.kind
        )
        switch ($kind.ToString()) {
            "Binary" { $value = [Convert]::FromBase64String([string]$Snapshot.value) }
            "None" { $value = [Convert]::FromBase64String([string]$Snapshot.value) }
            "MultiString" { $value = [string[]]@($Snapshot.value) }
            "DWord" { $value = [int]$Snapshot.value }
            "QWord" { $value = [long]$Snapshot.value }
            default { $value = [string]$Snapshot.value }
        }
        $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey([string]$Snapshot.key_path)
        try {
            $key.SetValue([string]$Snapshot.name, $value, $kind)
        }
        finally {
            $key.Dispose()
        }
        return
    }

    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
        [string]$Snapshot.key_path,
        $true
    )
    if ($null -ne $key) {
        try {
            if ($key.GetValueNames() -contains [string]$Snapshot.name) {
                $key.DeleteValue([string]$Snapshot.name, $false)
            }
        }
        finally {
            $key.Dispose()
        }
    }

    if (-not $Snapshot.key_existed) {
        $emptyKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
            [string]$Snapshot.key_path,
            $false
        )
        if ($null -ne $emptyKey) {
            try {
                $isEmpty = $emptyKey.ValueCount -eq 0 -and $emptyKey.SubKeyCount -eq 0
            }
            finally {
                $emptyKey.Dispose()
            }
            if ($isEmpty) {
                [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKey(
                    [string]$Snapshot.key_path,
                    $false
                )
            }
        }
    }
}

function Get-GsgHwpPaths {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$PackageRoot,
        [string]$LocalAppData = [Environment]::GetFolderPath("LocalApplicationData")
    )

    $root = (Resolve-Path -LiteralPath $PackageRoot).Path
    $manifestPath = Join-Path $root "compatibility-manifest.json"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $dataRoot = Join-Path $LocalAppData "GSG_HWP"
    $runtimeRoot = Join-Path $dataRoot "runtime"
    $runtimeVersionRoot = Join-Path $runtimeRoot ([string]$manifest.distribution)
    $nativeRoot = Join-Path $LocalAppData "HancomDocumentAutomation\native"
    $nativeVersionRoot = Join-Path $nativeRoot ([string]$manifest.native_bridge)
    $stateRoot = Join-Path $dataRoot "state"
    return [pscustomobject][ordered]@{
        PackageRoot = $root
        ManifestPath = $manifestPath
        SourceDll = Join-Path $root (
            "addon\HancomLiveBridgeNative\bin\{0}\HancomLiveBridge.dll" -f $manifest.native_bridge
        )
        NativeRoot = $nativeRoot
        NativeDll = Join-Path $nativeVersionRoot "HancomLiveBridge.dll"
        RuntimeRoot = $runtimeRoot
        RuntimeVersionRoot = $runtimeVersionRoot
        RuntimeEnvironment = Join-Path $runtimeVersionRoot ".venv"
        StateRoot = $stateRoot
        ActiveState = Join-Path $stateRoot "active-install.json"
        BackupsRoot = Join-Path $dataRoot "backups"
    }
}

function Test-GsgHwpPackage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        $Paths
    )

    $manifest = Get-Content -LiteralPath $Paths.ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $launcher = Join-Path $Paths.PackageRoot (
        "addon\HancomMcpLauncher\bin\Release\HancomMcpLauncher.exe"
    )
    $eventBridge = Join-Path $Paths.PackageRoot (
        "addon\HancomEventBridge\bin\Release\HancomEventBridge.exe"
    )
    foreach ($requiredPath in @(
        $Paths.SourceDll,
        $launcher,
        $eventBridge,
        (Join-Path $Paths.PackageRoot "uv.lock")
    )) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "Required package file is missing: $requiredPath"
        }
    }
    $launcherHash = (Get-FileHash -LiteralPath $launcher -Algorithm SHA256).Hash.ToLowerInvariant()
    $eventBridgeHash = (
        Get-FileHash -LiteralPath $eventBridge -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $nativeHash = (Get-FileHash -LiteralPath $Paths.SourceDll -Algorithm SHA256).Hash.ToLowerInvariant()
    if (
        $launcherHash -ne $manifest.launcher_sha256 -or
        $eventBridgeHash -ne $manifest.event_bridge_sha256 -or
        $nativeHash -ne $manifest.native_sha256
    ) {
        throw "Package checksum verification failed"
    }
    return $manifest
}

function New-GsgHwpBackup {
    param(
        [Parameter(Mandatory = $true)]
        $Paths,
        [Parameter(Mandatory = $true)]
        [string]$PackageVersion,
        [Parameter(Mandatory = $true)]
        [string]$ModulesKeyPath
    )

    $backupId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ") + "-" +
        [Guid]::NewGuid().ToString("N").Substring(0, 8)
    $backupDirectory = Join-Path $Paths.BackupsRoot $backupId
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    $originalDll = Join-Path $backupDirectory "original-HancomLiveBridge.dll"
    $dllExisted = Test-Path -LiteralPath $Paths.NativeDll -PathType Leaf
    if ($dllExisted) {
        Copy-Item -LiteralPath $Paths.NativeDll -Destination $originalDll -Force
    }
    $backup = [pscustomobject][ordered]@{
        schema_version = 1
        created_utc = [DateTime]::UtcNow.ToString("o")
        package_version = $PackageVersion
        registry = @(
            Get-GsgHwpRegistrySnapshot -KeyPath $ModulesKeyPath -Name $script:ModuleName
            Get-GsgHwpRegistrySnapshot -KeyPath "$ModulesKeyPath\Uses" -Name $script:ModuleName
        )
        dll = [pscustomobject][ordered]@{
            destination = $Paths.NativeDll
            existed = $dllExisted
            backup_file = if ($dllExisted) { $originalDll } else { $null }
        }
    }
    $backupFile = Join-Path $backupDirectory "original-state.json"
    Write-GsgHwpJson -Value $backup -Path $backupFile
    return $backupFile
}

function Restore-GsgHwpBackup {
    param(
        [Parameter(Mandatory = $true)]
        $Paths,
        [Parameter(Mandatory = $true)]
        [string]$BackupFile
    )

    $backup = Get-Content -LiteralPath $BackupFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $registrySnapshots = @($backup.registry)
    [array]::Reverse($registrySnapshots)
    foreach ($snapshot in $registrySnapshots) {
        Restore-GsgHwpRegistrySnapshot -Snapshot $snapshot
    }

    $destination = [string]$backup.dll.destination
    Assert-GsgHwpChildPath -Child $destination -Parent $Paths.NativeRoot
    if ($backup.dll.existed) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath ([string]$backup.dll.backup_file) -Destination $destination -Force
    }
    elseif (Test-Path -LiteralPath $destination -PathType Leaf) {
        Remove-Item -LiteralPath $destination -Force
    }
}

function Install-GsgHwpNative {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        $Paths,
        [Parameter(Mandatory = $true)]
        [string]$PackageVersion,
        [string]$ModulesKeyPath = $script:DefaultModulesKey
    )

    $createdBackup = -not (Test-Path -LiteralPath $Paths.ActiveState -PathType Leaf)
    if ($createdBackup) {
        $backupFile = New-GsgHwpBackup -Paths $Paths -PackageVersion $PackageVersion `
            -ModulesKeyPath $ModulesKeyPath
    }
    else {
        $activeState = Get-Content -LiteralPath $Paths.ActiveState -Raw -Encoding UTF8 | ConvertFrom-Json
        $backupFile = [string]$activeState.backup_file
        if (-not (Test-Path -LiteralPath $backupFile -PathType Leaf)) {
            throw "The active installation backup is missing: $backupFile"
        }
    }

    try {
        New-Item -ItemType Directory -Path (Split-Path -Parent $Paths.NativeDll) -Force | Out-Null
        Copy-Item -LiteralPath $Paths.SourceDll -Destination $Paths.NativeDll -Force
        $modulesKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($ModulesKeyPath)
        $usesKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("$ModulesKeyPath\Uses")
        try {
            $modulesKey.SetValue(
                $script:ModuleName,
                $Paths.NativeDll,
                [Microsoft.Win32.RegistryValueKind]::String
            )
            $usesKey.SetValue(
                $script:ModuleName,
                1,
                [Microsoft.Win32.RegistryValueKind]::DWord
            )
        }
        finally {
            $modulesKey.Dispose()
            $usesKey.Dispose()
        }
        $state = [pscustomobject][ordered]@{
            schema_version = 1
            package_version = $PackageVersion
            installed_utc = [DateTime]::UtcNow.ToString("o")
            backup_file = $backupFile
            native_dll = $Paths.NativeDll
        }
        Write-GsgHwpJson -Value $state -Path $Paths.ActiveState
    }
    catch {
        if ($createdBackup) {
            Restore-GsgHwpBackup -Paths $Paths -BackupFile $backupFile
        }
        throw
    }

    return [pscustomobject][ordered]@{
        Changed = $true
        BackupFile = $backupFile
        NativeDll = $Paths.NativeDll
    }
}

function Restore-GsgHwpNative {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        $Paths,
        [string]$ModulesKeyPath = $script:DefaultModulesKey
    )

    $null = $ModulesKeyPath
    if (-not (Test-Path -LiteralPath $Paths.ActiveState -PathType Leaf)) {
        return [pscustomobject][ordered]@{ Restored = $false; BackupFile = $null }
    }
    $activeState = Get-Content -LiteralPath $Paths.ActiveState -Raw -Encoding UTF8 | ConvertFrom-Json
    $backupFile = [string]$activeState.backup_file
    Restore-GsgHwpBackup -Paths $Paths -BackupFile $backupFile
    Remove-Item -LiteralPath $Paths.ActiveState -Force
    return [pscustomobject][ordered]@{ Restored = $true; BackupFile = $backupFile }
}

function Remove-GsgHwpRuntime {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        $Paths
    )

    Assert-GsgHwpChildPath -Child $Paths.RuntimeVersionRoot -Parent $Paths.RuntimeRoot
    if (Test-Path -LiteralPath $Paths.RuntimeVersionRoot -PathType Container) {
        Remove-Item -LiteralPath $Paths.RuntimeVersionRoot -Recurse -Force
    }
}

function Test-GsgHwpStopped {
    return @(Get-Process -Name "Hwp" -ErrorAction SilentlyContinue).Count -eq 0
}

Export-ModuleMember -Function @(
    "Get-GsgHwpPaths",
    "Install-GsgHwpNative",
    "Remove-GsgHwpRuntime",
    "Restore-GsgHwpNative",
    "Test-GsgHwpPackage",
    "Test-GsgHwpStopped"
)
