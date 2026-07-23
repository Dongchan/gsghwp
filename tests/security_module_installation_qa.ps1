#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-True {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$Condition,
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Assert-Equal {
    param(
        [Parameter(Mandatory = $true)]
        $Expected,
        [Parameter(Mandatory = $true)]
        $Actual,
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if ($Expected -ne $Actual) {
        throw "$Message (expected=$Expected, actual=$Actual)"
    }
}

function New-SecurityFixture {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PluginRoot,
        [Parameter(Mandatory = $true)]
        [string]$TestId
    )

    $registryRoot = "Software\GSG_HWP_SecurityQa_$TestId"
    $localAppData = Join-Path ([System.IO.Path]::GetTempPath()) "GsgHwpSecurityQa-$TestId"
    $paths = Get-GsgHwpPaths -PackageRoot $PluginRoot -LocalAppData $localAppData
    Assert-True -Condition ($paths.PSObject.Properties.Name -contains "SecuritySourceDll") `
        -Message "Security source DLL path is missing from installer paths"
    Assert-True -Condition ($paths.PSObject.Properties.Name -contains "SecurityDll") `
        -Message "Security destination DLL path is missing from installer paths"

    New-Item -ItemType Directory -Path (Split-Path -Parent $paths.SecuritySourceDll) -Force |
        Out-Null
    [System.IO.File]::WriteAllBytes($paths.SecuritySourceDll, [byte[]](91, 92, 93, 94))

    return [pscustomobject][ordered]@{
        RegistryRoot = $registryRoot
        ModulesKey = "$registryRoot\UserActionModules"
        AutomationModulesKey = "$registryRoot\AutomationModules"
        LocalAppData = $localAppData
        Paths = $paths
    }
}

function Remove-SecurityFixture {
    param(
        [Parameter(Mandatory = $true)]
        $Fixture
    )

    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($Fixture.RegistryRoot, $false)
    if (Test-Path -LiteralPath $Fixture.LocalAppData) {
        $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        $resolvedTarget = [System.IO.Path]::GetFullPath($Fixture.LocalAppData)
        Assert-True -Condition $resolvedTarget.StartsWith(
            $resolvedTemp,
            [StringComparison]::OrdinalIgnoreCase
        ) -Message "QA cleanup target escaped the temporary directory"
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$pluginRoot = Join-Path $repositoryRoot "plugins\gsg-hwp"
$modulePath = Join-Path $pluginRoot "scripts\GsgHwp.Installation.psm1"
Import-Module -Name $modulePath -Force

# Existing security DLL and registry values must round-trip exactly.
$fixture = New-SecurityFixture -PluginRoot $pluginRoot -TestId ([Guid]::NewGuid().ToString("N"))
try {
    $fixtureSecurityHash = (
        Get-FileHash -LiteralPath $fixture.Paths.SecuritySourceDll -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    Assert-Equal -Expected $fixture.Paths.SecuritySourceDll `
        -Actual (Test-GsgHwpSecurityModule -Paths $fixture.Paths `
            -ExpectedSha256 $fixtureSecurityHash) `
        -Message "Matching security DLL checksum was not accepted"
    $checksumRejected = $false
    try {
        $null = Test-GsgHwpSecurityModule -Paths $fixture.Paths -ExpectedSha256 ("0" * 64)
    }
    catch {
        $checksumRejected = $_.Exception.Message -eq (
            "FilePathCheckerModule.dll checksum verification failed"
        )
    }
    Assert-True -Condition $checksumRejected `
        -Message "Mismatched security DLL checksum was not rejected"

    $originalNative = [byte[]](10, 20, 30, 40)
    $originalSecurity = [byte[]](50, 60, 70, 80)
    New-Item -ItemType Directory -Path (Split-Path -Parent $fixture.Paths.NativeDll) -Force |
        Out-Null
    New-Item -ItemType Directory -Path (Split-Path -Parent $fixture.Paths.SecurityDll) -Force |
        Out-Null
    [System.IO.File]::WriteAllBytes($fixture.Paths.NativeDll, $originalNative)
    [System.IO.File]::WriteAllBytes($fixture.Paths.SecurityDll, $originalSecurity)

    $modules = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($fixture.ModulesKey)
    $automation = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey(
        $fixture.AutomationModulesKey
    )
    try {
        $modules.SetValue(
            "한컴브릿지",
            "original-bridge.dll",
            [Microsoft.Win32.RegistryValueKind]::String
        )
        $automation.SetValue(
            "FilePathCheckerModule",
            "original-security.dll",
            [Microsoft.Win32.RegistryValueKind]::ExpandString
        )
    }
    finally {
        $modules.Dispose()
        $automation.Dispose()
    }

    $install = Install-GsgHwpNative -Paths $fixture.Paths -PackageVersion "1.0.4" `
        -ModulesKeyPath $fixture.ModulesKey `
        -AutomationModulesKeyPath $fixture.AutomationModulesKey
    Assert-Equal -Expected $fixture.Paths.SecurityDll -Actual $install.SecurityDll `
        -Message "Security install result did not report the destination DLL"
    Assert-Equal -Expected "91,92,93,94" `
        -Actual (([System.IO.File]::ReadAllBytes($fixture.Paths.SecurityDll)) -join ",") `
        -Message "Security DLL was not installed"

    $installedAutomation = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
        $fixture.AutomationModulesKey
    )
    try {
        Assert-Equal -Expected $fixture.Paths.SecurityDll `
            -Actual $installedAutomation.GetValue("FilePathCheckerModule") `
            -Message "FilePathCheckerModule registry path was not installed"
        Assert-Equal -Expected ([Microsoft.Win32.RegistryValueKind]::String) `
            -Actual $installedAutomation.GetValueKind("FilePathCheckerModule") `
            -Message "FilePathCheckerModule registry value kind must be String"
    }
    finally {
        $installedAutomation.Dispose()
    }

    $backup = Get-Content -LiteralPath $install.BackupFile -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-Equal -Expected 2 -Actual $backup.schema_version `
        -Message "Security-aware backup schema was not written"
    Assert-Equal -Expected 3 -Actual @($backup.registry).Count `
        -Message "Security registry state was not included in the backup"
    Assert-Equal -Expected $fixture.Paths.SecurityDll -Actual $backup.security_dll.destination `
        -Message "Security DLL destination was not included in the backup"

    $restore = Restore-GsgHwpNative -Paths $fixture.Paths `
        -ModulesKeyPath $fixture.ModulesKey `
        -AutomationModulesKeyPath $fixture.AutomationModulesKey
    Assert-True -Condition $restore.Restored -Message "Security-aware restore did not run"
    Assert-Equal -Expected ($originalSecurity -join ",") `
        -Actual (([System.IO.File]::ReadAllBytes($fixture.Paths.SecurityDll)) -join ",") `
        -Message "Original security DLL was not restored"

    $restoredAutomation = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
        $fixture.AutomationModulesKey
    )
    try {
        Assert-Equal -Expected "original-security.dll" `
            -Actual $restoredAutomation.GetValue(
                "FilePathCheckerModule",
                $null,
                [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
            ) `
            -Message "Original security registry value was not restored"
        Assert-Equal -Expected ([Microsoft.Win32.RegistryValueKind]::ExpandString) `
            -Actual $restoredAutomation.GetValueKind("FilePathCheckerModule") `
            -Message "Original security registry kind was not restored"
    }
    finally {
        $restoredAutomation.Dispose()
    }
}
finally {
    Remove-SecurityFixture -Fixture $fixture
}

# A clean PC must receive the security module, and uninstall must remove only what GSG HWP added.
$cleanFixture = New-SecurityFixture -PluginRoot $pluginRoot -TestId ([Guid]::NewGuid().ToString("N"))
try {
    $null = Install-GsgHwpNative -Paths $cleanFixture.Paths -PackageVersion "1.0.4" `
        -ModulesKeyPath $cleanFixture.ModulesKey `
        -AutomationModulesKeyPath $cleanFixture.AutomationModulesKey
    Assert-True -Condition (Test-Path -LiteralPath $cleanFixture.Paths.SecurityDll -PathType Leaf) `
        -Message "Clean-PC install did not add FilePathCheckerModule.dll"

    $null = Restore-GsgHwpNative -Paths $cleanFixture.Paths `
        -ModulesKeyPath $cleanFixture.ModulesKey `
        -AutomationModulesKeyPath $cleanFixture.AutomationModulesKey
    Assert-True -Condition (-not (Test-Path -LiteralPath $cleanFixture.Paths.SecurityDll)) `
        -Message "Clean-PC restore did not remove the security DLL added by GSG HWP"
    $restoredAutomation = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
        $cleanFixture.AutomationModulesKey
    )
    Assert-True -Condition (
        $null -eq $restoredAutomation -or
        $restoredAutomation.GetValueNames() -notcontains "FilePathCheckerModule"
    ) -Message "Clean-PC restore did not remove the security registry value added by GSG HWP"
    if ($null -ne $restoredAutomation) {
        $restoredAutomation.Dispose()
    }
}
finally {
    Remove-SecurityFixture -Fixture $cleanFixture
}

# Updating a v1.0.0 active install must preserve its bridge baseline and add a security baseline.
$upgradeFixture = New-SecurityFixture -PluginRoot $pluginRoot -TestId ([Guid]::NewGuid().ToString("N"))
try {
    $backupDirectory = Join-Path $upgradeFixture.Paths.BackupsRoot "v1-baseline"
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    $backupFile = Join-Path $backupDirectory "original-state.json"
    $legacyBackup = [pscustomobject][ordered]@{
        schema_version = 1
        created_utc = [DateTime]::UtcNow.ToString("o")
        package_version = "1.0.0"
        registry = @(
            [pscustomobject][ordered]@{
                key_path = $upgradeFixture.ModulesKey
                name = "한컴브릿지"
                key_existed = $false
                value_existed = $false
                kind = $null
                value = $null
            },
            [pscustomobject][ordered]@{
                key_path = "$($upgradeFixture.ModulesKey)\Uses"
                name = "한컴브릿지"
                key_existed = $false
                value_existed = $false
                kind = $null
                value = $null
            }
        )
        dll = [pscustomobject][ordered]@{
            destination = $upgradeFixture.Paths.NativeDll
            existed = $false
            backup_file = $null
        }
    }
    New-Item -ItemType Directory -Path $upgradeFixture.Paths.StateRoot -Force | Out-Null
    $legacyBackup | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $backupFile -Encoding UTF8
    [pscustomobject][ordered]@{
        schema_version = 1
        package_version = "1.0.0"
        backup_file = $backupFile
        native_dll = $upgradeFixture.Paths.NativeDll
    } | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $upgradeFixture.Paths.ActiveState -Encoding UTF8

    $upgradeOriginalSecurity = [byte[]](21, 22, 23, 24)
    New-Item -ItemType Directory -Path (Split-Path -Parent $upgradeFixture.Paths.SecurityDll) `
        -Force | Out-Null
    [System.IO.File]::WriteAllBytes(
        $upgradeFixture.Paths.SecurityDll,
        $upgradeOriginalSecurity
    )
    $automation = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey(
        $upgradeFixture.AutomationModulesKey
    )
    try {
        $automation.SetValue(
            "FilePathCheckerModule",
            "pre-v1.0.1-security.dll",
            [Microsoft.Win32.RegistryValueKind]::String
        )
    }
    finally {
        $automation.Dispose()
    }

    $null = Install-GsgHwpNative -Paths $upgradeFixture.Paths -PackageVersion "1.0.4" `
        -ModulesKeyPath $upgradeFixture.ModulesKey `
        -AutomationModulesKeyPath $upgradeFixture.AutomationModulesKey
    $migrated = Get-Content -LiteralPath $backupFile -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-Equal -Expected 2 -Actual $migrated.schema_version `
        -Message "v1.0.0 backup was not upgraded before security installation"
    Assert-Equal -Expected 3 -Actual @($migrated.registry).Count `
        -Message "v1.0.0 backup did not gain the security registry snapshot"

    $null = Restore-GsgHwpNative -Paths $upgradeFixture.Paths `
        -ModulesKeyPath $upgradeFixture.ModulesKey `
        -AutomationModulesKeyPath $upgradeFixture.AutomationModulesKey
    Assert-Equal -Expected ($upgradeOriginalSecurity -join ",") `
        -Actual (([System.IO.File]::ReadAllBytes($upgradeFixture.Paths.SecurityDll)) -join ",") `
        -Message "Upgrade restore did not recover the pre-v1.0.1 security DLL"
    $automation = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
        $upgradeFixture.AutomationModulesKey
    )
    try {
        Assert-Equal -Expected "pre-v1.0.1-security.dll" `
            -Actual $automation.GetValue("FilePathCheckerModule") `
            -Message "Upgrade restore did not recover the pre-v1.0.1 security registry value"
    }
    finally {
        $automation.Dispose()
    }
}
finally {
    Remove-SecurityFixture -Fixture $upgradeFixture
}

Write-Output "PASS: FilePathCheckerModule clean install, exact restore, and v1.0.0 backup upgrade"
