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

function Assert-Throws {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Action,
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    try {
        & $Action
    }
    catch {
        return
    }
    throw $Message
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$pluginRoot = Join-Path $repositoryRoot "plugins\gsg-hwp"
$modulePath = Join-Path $pluginRoot "scripts\GsgHwp.Update.psm1"
$policyPath = Join-Path $pluginRoot "update-policy.json"

Assert-True -Condition (Test-Path -LiteralPath $modulePath -PathType Leaf) `
    -Message "Automatic update module is missing"
Assert-True -Condition (Test-Path -LiteralPath $policyPath -PathType Leaf) `
    -Message "Automatic update policy is missing"

Import-Module -Name $modulePath -Force

Assert-Equal -Expected 1 -Actual (Compare-GsgHwpVersion -Left "1.1.0" -Right "1.0.4") `
    -Message "Newer version comparison failed"
Assert-Equal -Expected 0 -Actual (Compare-GsgHwpVersion -Left "1.1.0" -Right "1.1.0") `
    -Message "Equal version comparison failed"
Assert-Equal -Expected -1 -Actual (Compare-GsgHwpVersion -Left "1.0.4" -Right "1.1.0") `
    -Message "Older version comparison failed"

$policy = Read-GsgHwpUpdatePolicy -Path $policyPath
Assert-True -Condition $policy.enabled -Message "Automatic update must be enabled"
Assert-Equal -Expected "stable" -Actual $policy.channel -Message "Update channel mismatch"
Assert-True -Condition $policy.manifest_url.StartsWith(
    "https://github.com/innae1121-bit/gsghwp/releases/"
) -Message "Update manifest must use the official GitHub repository"
$installSource = Get-Content -LiteralPath (Join-Path $repositoryRoot "install.ps1") -Raw -Encoding UTF8
$updaterSource = Get-Content -LiteralPath $modulePath -Raw -Encoding UTF8
$launcherSource = Get-Content -LiteralPath (
    Join-Path $pluginRoot "scripts\start-mcp.ps1"
) -Raw -Encoding UTF8
Assert-True -Condition ($installSource -match "--managed-python") `
    -Message "Initial installation must use an uv-managed Python"
Assert-True -Condition ($updaterSource -match "--managed-python") `
    -Message "Automatic updates must use an uv-managed Python"
Assert-True -Condition ($launcherSource -match "\.venv\\Scripts\\python\.exe") `
    -Message "The MCP must run from its version-specific .venv"
Assert-True -Condition ($launcherSource -notmatch "Get-Command\s+['`"]?python") `
    -Message "The MCP launcher must not fall back to a system Python"

$validManifest = [pscustomobject][ordered]@{
    schema_version = 1
    distribution = "1.1.1"
    source_version = "0.5.74"
    package_url = "https://github.com/innae1121-bit/gsghwp/releases/download/v1.1.1/gsg-hwp-plugin-v1.1.1.zip"
    package_sha256 = ("a" * 64)
    package_root = "gsg-hwp"
    native_bridge = "0.5.122"
    published_utc = "2026-07-25T00:00:00Z"
}
Assert-True -Condition (Test-GsgHwpUpdateManifest -Manifest $validManifest) `
    -Message "Valid update manifest was rejected"

$invalidUrlManifest = $validManifest.PSObject.Copy()
$invalidUrlManifest.package_url = "https://example.com/gsg-hwp.zip"
Assert-Throws -Action {
    $null = Test-GsgHwpUpdateManifest -Manifest $invalidUrlManifest
} -Message "Untrusted package URL was accepted"

$invalidHashManifest = $validManifest.PSObject.Copy()
$invalidHashManifest.package_sha256 = "not-a-sha256"
Assert-Throws -Action {
    $null = Test-GsgHwpUpdateManifest -Manifest $invalidHashManifest
} -Message "Invalid package hash was accepted"

$testId = [Guid]::NewGuid().ToString("N")
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) "GsgHwpUpdateQa-$testId"
try {
    $localAppData = Join-Path $temporaryRoot "LocalAppData"
    $packagesRoot = Join-Path $localAppData "GSG_HWP\packages"
    $candidateRoot = Join-Path $packagesRoot "1.1.1\gsg-hwp"
    New-Item -ItemType Directory -Path $candidateRoot -Force | Out-Null
    [pscustomobject][ordered]@{ distribution = "1.1.1" } |
        ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $candidateRoot "compatibility-manifest.json") `
            -Encoding UTF8

    $stateRoot = Join-Path $localAppData "GSG_HWP\updater"
    New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
    [pscustomobject][ordered]@{
        schema_version = 1
        distribution = "1.1.1"
        package_root = $candidateRoot
        previous_package_root = $pluginRoot
    } |
        ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $stateRoot "active-package.json") -Encoding UTF8

    Assert-Equal -Expected $candidateRoot `
        -Actual (Get-GsgHwpActivePackageRoot -BootstrapRoot $pluginRoot `
            -LocalAppData $localAppData) `
        -Message "Validated active package was not selected"

    $outsideRoot = Join-Path $temporaryRoot "outside"
    New-Item -ItemType Directory -Path $outsideRoot -Force | Out-Null
    [pscustomobject][ordered]@{ distribution = "9.9.9" } |
        ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $outsideRoot "compatibility-manifest.json") `
            -Encoding UTF8
    [pscustomobject][ordered]@{
        schema_version = 1
        distribution = "9.9.9"
        package_root = $outsideRoot
        previous_package_root = $pluginRoot
    } |
        ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $stateRoot "active-package.json") -Encoding UTF8

    Assert-Equal -Expected $pluginRoot `
        -Actual (Get-GsgHwpActivePackageRoot -BootstrapRoot $pluginRoot `
            -LocalAppData $localAppData) `
        -Message "Package path outside the managed directory was accepted"

    Add-Type -AssemblyName System.IO.Compression
    $maliciousZip = Join-Path $temporaryRoot "malicious.zip"
    $zipStream = [System.IO.File]::Open(
        $maliciousZip,
        [System.IO.FileMode]::Create,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
    try {
        $zipArchive = New-Object System.IO.Compression.ZipArchive(
            $zipStream,
            [System.IO.Compression.ZipArchiveMode]::Create,
            $true
        )
        try {
            $entry = $zipArchive.CreateEntry("../escaped.txt")
            $writer = New-Object System.IO.StreamWriter($entry.Open())
            try {
                $writer.Write("blocked")
            }
            finally {
                $writer.Dispose()
            }
        }
        finally {
            $zipArchive.Dispose()
        }
    }
    finally {
        $zipStream.Dispose()
    }

    $extractRoot = Join-Path $temporaryRoot "extract"
    Assert-Throws -Action {
        Expand-GsgHwpUpdateArchive -ArchivePath $maliciousZip -Destination $extractRoot
    } -Message "Archive path traversal entry was accepted"
    Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $temporaryRoot "escaped.txt"))) `
        -Message "Archive path traversal escaped the staging directory"
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        $resolvedTarget = [System.IO.Path]::GetFullPath($temporaryRoot)
        Assert-True -Condition $resolvedTarget.StartsWith(
            $resolvedTemp,
            [StringComparison]::OrdinalIgnoreCase
        ) -Message "QA cleanup target escaped the temporary directory"
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

Write-Output "PASS: automatic update policy, trusted releases, managed .venv selection, zip traversal protection"
