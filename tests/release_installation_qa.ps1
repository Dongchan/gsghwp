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

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$pluginRoot = Join-Path $repositoryRoot "plugins\gsg-hwp"
$modulePath = Join-Path $pluginRoot "scripts\GsgHwp.Installation.psm1"
$requiredFiles = @(
    ".agents\plugins\marketplace.json",
    ".github\workflows\publish-release.yml",
    ".mcp.json",
    "AGENTS.md",
    "CLAUDE.md",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "restore-update.ps1",
    "THIRD_PARTY_NOTICES.md",
    "install.ps1",
    "uninstall.ps1",
    "plugins\gsg-hwp\.codex-plugin\plugin.json",
    "plugins\gsg-hwp\.mcp.json",
    "plugins\gsg-hwp\compatibility-manifest.json",
    "plugins\gsg-hwp\LICENSE",
    "plugins\gsg-hwp\THIRD_PARTY_NOTICES.md",
    "plugins\gsg-hwp\scripts\start-mcp.ps1",
    "plugins\gsg-hwp\scripts\GsgHwp.Update.psm1",
    "plugins\gsg-hwp\update-policy.json"
)

foreach ($relativePath in $requiredFiles) {
    Assert-True -Condition (Test-Path -LiteralPath (Join-Path $repositoryRoot $relativePath)) `
        -Message "Required release file is missing: $relativePath"
}

$powerShellFiles = Get-ChildItem -LiteralPath $repositoryRoot -Recurse -File |
    Where-Object { $_.Extension -in @(".ps1", ".psm1") }
foreach ($powerShellFile in $powerShellFiles) {
    $tokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $powerShellFile.FullName,
        [ref]$tokens,
        [ref]$parseErrors
    )
    Assert-Equal -Expected 0 -Actual @($parseErrors).Count `
        -Message "PowerShell parse error in $($powerShellFile.FullName)"
}

$plugin = Get-Content -LiteralPath (Join-Path $pluginRoot ".codex-plugin\plugin.json") -Raw -Encoding UTF8 |
    ConvertFrom-Json
$marketplace = Get-Content -LiteralPath (Join-Path $repositoryRoot ".agents\plugins\marketplace.json") -Raw -Encoding UTF8 |
    ConvertFrom-Json
$manifest = Get-Content -LiteralPath (Join-Path $pluginRoot "compatibility-manifest.json") -Raw -Encoding UTF8 |
    ConvertFrom-Json
$mcp = Get-Content -LiteralPath (Join-Path $pluginRoot ".mcp.json") -Raw -Encoding UTF8 |
    ConvertFrom-Json
$projectMetadata = Get-Content -LiteralPath (Join-Path $pluginRoot "pyproject.toml") -Raw -Encoding UTF8
$pluginVersion = [string]$plugin.version
$projectBlock = [regex]::Match(
    $projectMetadata,
    '(?ms)^\[project\]\s*(?<body>.*?)(?=^\[|\z)'
)
$projectVersionMatch = [regex]::Match(
    $projectBlock.Groups["body"].Value,
    '(?m)^version\s*=\s*"(?<version>[^"]+)"\s*$'
)

Assert-Equal -Expected "gsg-hwp" -Actual $plugin.name -Message "Plugin name mismatch"
Assert-True -Condition ($pluginVersion -match '^\d+\.\d+\.\d+$') `
    -Message "Plugin version must use major.minor.patch"
Assert-Equal -Expected "inodesign" -Actual $plugin.author.name -Message "Plugin author mismatch"
Assert-Equal -Expected "inodesign" -Actual $plugin.interface.developerName `
    -Message "Plugin developer metadata mismatch"
Assert-Equal -Expected $pluginVersion -Actual ([string]$manifest.distribution) `
    -Message "Plugin and compatibility manifest versions differ"
Assert-True -Condition (
    [string]$manifest.source_version -match '^\d+\.\d+\.\d+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$'
) -Message "Source version format mismatch"
Assert-Equal -Expected "inodesign" -Actual $manifest.developer -Message "Manifest developer mismatch"
Assert-Equal -Expected "MIT" -Actual $manifest.license -Message "Distribution license mismatch"
Assert-Equal -Expected "LICENSE" -Actual $manifest.license_file -Message "License file metadata mismatch"
Assert-Equal -Expected "THIRD_PARTY_NOTICES.md" -Actual $manifest.third_party_notices `
    -Message "Third-party notices metadata mismatch"
Assert-Equal -Expected "All-Rights-Reserved" -Actual $manifest.media_license `
    -Message "Demo media license metadata mismatch"
Assert-True -Condition $manifest.hancom_automation_commercial_approval_required `
    -Message "Hancom commercial approval metadata mismatch"
Assert-True -Condition ($projectMetadata -match '(?m)^license = "MIT"\s*$') `
    -Message "Python project license metadata mismatch"
$rootLicenseHash = (Get-FileHash -LiteralPath (Join-Path $repositoryRoot "LICENSE") `
    -Algorithm SHA256).Hash
$pluginLicenseHash = (Get-FileHash -LiteralPath (Join-Path $pluginRoot "LICENSE") `
    -Algorithm SHA256).Hash
Assert-Equal -Expected $rootLicenseHash -Actual $pluginLicenseHash `
    -Message "Root and plugin license copies differ"
$rootNoticesHash = (Get-FileHash -LiteralPath (Join-Path $repositoryRoot "THIRD_PARTY_NOTICES.md") `
    -Algorithm SHA256).Hash
$pluginNoticesHash = (Get-FileHash -LiteralPath (Join-Path $pluginRoot "THIRD_PARTY_NOTICES.md") `
    -Algorithm SHA256).Hash
Assert-Equal -Expected $rootNoticesHash -Actual $pluginNoticesHash `
    -Message "Root and plugin notice copies differ"
Assert-True -Condition $projectBlock.Success -Message "Python project metadata is missing"
Assert-True -Condition $projectVersionMatch.Success -Message "Python project version is missing"
Assert-Equal -Expected $projectVersionMatch.Groups["version"].Value `
    -Actual ([string]$manifest.mcp) -Message "MCP and Python project versions differ"
Assert-True -Condition (
    [string]$manifest.native_bridge -match '^\d+\.\d+\.\d+$'
) -Message "Native bridge version format mismatch"
Assert-Equal -Expected 44 -Actual @($manifest.production_tools).Count `
    -Message "Production tool count mismatch"
Assert-True -Condition ($manifest.production_tools -contains "hwp_insert_layout") `
    -Message "Mid-document layout tool is missing"
Assert-True -Condition ($manifest.production_tools -contains "hwp_list_window_states") `
    -Message "Window-state tool is missing"
Assert-Equal -Expected 45 -Actual $manifest.exposed_tool_count -Message "Exposed tool count mismatch"
Assert-Equal -Expected 62 -Actual $manifest.qa_tool_count -Message "QA tool count mismatch"
Assert-Equal -Expected "hwp_reload" -Actual $manifest.runtime_tools[0] `
    -Message "Runtime reload tool mismatch"
Assert-Equal -Expected 1452 -Actual $manifest.official_api_catalog_entries `
    -Message "Official API catalog count mismatch"
Assert-Equal -Expected 1448 -Actual $manifest.official_api_enabled_routes `
    -Message "Official API enabled route count mismatch"
Assert-Equal -Expected 4 -Actual @($manifest.official_api_disabled_case_ids).Count `
    -Message "Disabled official API count mismatch"
Assert-Equal -Expected "FilePathCheckerModule" -Actual $manifest.file_path_checker_module `
    -Message "Security module name mismatch"
Assert-Equal -Expected "pyhwpx==1.6.6" -Actual $manifest.file_path_checker_source `
    -Message "Security module source mismatch"
Assert-Equal -Expected "9ac5b97c47ac8aed1e8bca27a3eef39411361d8f68c262509f0c40a8f9d21bb6" `
    -Actual $manifest.file_path_checker_sha256 -Message "Security module checksum mismatch"
$expectedDisabledApi = @(
    "action:0608:SaveHistoryItem",
    "automation:0067:IHwpObject.ExportStyle",
    "automation:0068:IHwpObject.ImportStyle",
    "automation:0365:IDHwpParameterArray.Clone"
) | Sort-Object
Assert-Equal -Expected ($expectedDisabledApi -join "|") `
    -Actual ((@($manifest.official_api_disabled_case_ids) | Sort-Object) -join "|") `
    -Message "Disabled official API identifiers mismatch"
Assert-Equal -Expected "gsg-hwp" -Actual $marketplace.plugins[0].name -Message "Marketplace plugin mismatch"
Assert-Equal -Expected "./plugins/gsg-hwp" -Actual $marketplace.plugins[0].source.path `
    -Message "Marketplace source path mismatch"
Assert-Equal -Expected "powershell.exe" -Actual $mcp.mcpServers."gsg-hwp".command `
    -Message "MCP must start through the portable PowerShell launcher"
$installSource = Get-Content -LiteralPath (Join-Path $repositoryRoot "install.ps1") -Raw -Encoding UTF8
$updaterSource = Get-Content -LiteralPath (
    Join-Path $pluginRoot "scripts\GsgHwp.Update.psm1"
) -Raw -Encoding UTF8
$launcherSource = Get-Content -LiteralPath (
    Join-Path $pluginRoot "scripts\start-mcp.ps1"
) -Raw -Encoding UTF8
Assert-True -Condition ($installSource -match "--managed-python") `
    -Message "Installer must require an uv-managed Python"
Assert-True -Condition ($updaterSource -match "--managed-python") `
    -Message "Updater must require an uv-managed Python"
Assert-True -Condition ($launcherSource -match "\.venv\\Scripts\\python\.exe") `
    -Message "MCP launcher must use the versioned virtual environment"
Assert-True -Condition ($launcherSource -notmatch "Get-Command\s+['`"]?python") `
    -Message "MCP launcher must not fall back to a system Python"

$launcher = Join-Path $pluginRoot "addon\HancomMcpLauncher\bin\Release\HancomMcpLauncher.exe"
$eventBridge = Join-Path $pluginRoot "addon\HancomEventBridge\bin\Release\HancomEventBridge.exe"
$nativeDll = Join-Path $pluginRoot (
    "addon\HancomLiveBridgeNative\bin\{0}\HancomLiveBridge.dll" -f $manifest.native_bridge
)
Assert-Equal -Expected $manifest.launcher_sha256 `
    -Actual (Get-FileHash -LiteralPath $launcher -Algorithm SHA256).Hash.ToLowerInvariant() `
    -Message "Launcher checksum mismatch"
Assert-Equal -Expected $manifest.event_bridge_sha256 `
    -Actual (Get-FileHash -LiteralPath $eventBridge -Algorithm SHA256).Hash.ToLowerInvariant() `
    -Message "Event bridge checksum mismatch"
Assert-Equal -Expected $manifest.native_sha256 `
    -Actual (Get-FileHash -LiteralPath $nativeDll -Algorithm SHA256).Hash.ToLowerInvariant() `
    -Message "Native bridge checksum mismatch"

$forbiddenPaths = @(
    ("C:" + "\Users\"),
    ("C:" + "/Users/"),
    ("C:" + "\Work\"),
    ("C:" + "/Work/"),
    ("\Admin" + "istrator\"),
    ("/Admin" + "istrator/")
)
$forbiddenTerms = @(
    ("테스트" + "파일"),
    ("미아제" + "11"),
    ("한양" + "시장"),
    ("행당" + "동")
)
$latin1 = [System.Text.Encoding]::GetEncoding(28591)
$utf8 = [System.Text.Encoding]::UTF8
$unicode = [System.Text.Encoding]::Unicode
$releaseFiles = Get-ChildItem -LiteralPath $repositoryRoot -Recurse -File |
    Where-Object {
        $_.FullName -notmatch "[\\/](\.git|\.venv|__pycache__)[\\/]"
    }

foreach ($file in $releaseFiles) {
    $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
    $singleByteText = $latin1.GetString($bytes)
    $utf8Text = $utf8.GetString($bytes)
    $unicodeText = $unicode.GetString($bytes)
    foreach ($forbiddenPath in $forbiddenPaths) {
        Assert-True -Condition (-not $singleByteText.Contains($forbiddenPath)) `
            -Message "Personal path found in $($file.FullName)"
        Assert-True -Condition (-not $unicodeText.Contains($forbiddenPath)) `
            -Message "Personal path found in $($file.FullName)"
        Assert-True -Condition (-not $utf8Text.Contains($forbiddenPath)) `
            -Message "Personal path found in $($file.FullName)"
    }
    foreach ($forbiddenTerm in $forbiddenTerms) {
        Assert-True -Condition (-not $singleByteText.Contains($forbiddenTerm)) `
            -Message "Personal term found in $($file.FullName)"
        Assert-True -Condition (-not $unicodeText.Contains($forbiddenTerm)) `
            -Message "Personal term found in $($file.FullName)"
        Assert-True -Condition (-not $utf8Text.Contains($forbiddenTerm)) `
            -Message "Personal term found in $($file.FullName)"
    }
}

$forbiddenArtifacts = Get-ChildItem -LiteralPath $repositoryRoot -Recurse -File |
    Where-Object {
        $_.Extension -in @(
            ".pdb", ".pyc", ".pyo", ".obj", ".lib", ".exp", ".ilk",
            ".iobj", ".ipdb", ".tlog", ".log"
        )
    }
Assert-Equal -Expected 0 -Actual @($forbiddenArtifacts).Count `
    -Message "Debug or cache artifacts remain in the release"

$forbiddenDirectories = Get-ChildItem -LiteralPath $repositoryRoot -Recurse -Directory -Force |
    Where-Object {
        $_.Name -in @(".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "obj")
    }
Assert-Equal -Expected 0 -Actual @($forbiddenDirectories).Count `
    -Message "Generated directories remain in the release"

Import-Module -Name $modulePath -Force
$testId = [Guid]::NewGuid().ToString("N")
$registryRoot = "Software\GSG_HWP_ReleaseQa_$testId"
$modulesKey = "$registryRoot\Modules"
$automationModulesKey = "$registryRoot\AutomationModules"
$localAppData = Join-Path ([System.IO.Path]::GetTempPath()) "GsgHwpReleaseQa-$testId"
$paths = Get-GsgHwpPaths -PackageRoot $pluginRoot -LocalAppData $localAppData
Assert-Equal -Expected (
    Join-Path $localAppData ("GSG_HWP\runtime\{0}\.venv" -f $pluginVersion)
) -Actual $paths.RuntimeEnvironment `
    -Message "Runtime must use a distribution-specific .venv"
$originalDll = [byte[]](10, 20, 30, 40)
$originalSecurityDll = [byte[]](50, 60, 70, 80)

try {
    New-Item -ItemType Directory -Path $paths.RuntimeVersionRoot -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $paths.RuntimeVersionRoot "qa-runtime.txt") `
        -Value "isolated" -Encoding ASCII
    New-Item -ItemType Directory -Path (Split-Path -Parent $paths.NativeDll) -Force | Out-Null
    [System.IO.File]::WriteAllBytes($paths.NativeDll, $originalDll)
    New-Item -ItemType Directory -Path (Split-Path -Parent $paths.SecuritySourceDll) -Force |
        Out-Null
    New-Item -ItemType Directory -Path (Split-Path -Parent $paths.SecurityDll) -Force |
        Out-Null
    [System.IO.File]::WriteAllBytes($paths.SecuritySourceDll, [byte[]](91, 92, 93, 94))
    [System.IO.File]::WriteAllBytes($paths.SecurityDll, $originalSecurityDll)

    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($modulesKey)
    try {
        $key.SetValue("한컴브릿지", "original-bridge.dll", [Microsoft.Win32.RegistryValueKind]::String)
    }
    finally {
        $key.Dispose()
    }
    $automationKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($automationModulesKey)
    try {
        $automationKey.SetValue(
            "FilePathCheckerModule",
            "original-security.dll",
            [Microsoft.Win32.RegistryValueKind]::String
        )
    }
    finally {
        $automationKey.Dispose()
    }

    $installResult = Install-GsgHwpNative -Paths $paths -PackageVersion $pluginVersion `
        -ModulesKeyPath $modulesKey -AutomationModulesKeyPath $automationModulesKey
    Assert-True -Condition $installResult.Changed -Message "Native install did not report a change"
    Assert-True -Condition (Test-Path -LiteralPath $paths.ActiveState) `
        -Message "Active installation state was not written"

    $installedKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($modulesKey)
    $installedUsesKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("$modulesKey\Uses")
    $installedAutomationKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
        $automationModulesKey
    )
    try {
        Assert-Equal -Expected $paths.NativeDll -Actual $installedKey.GetValue("한컴브릿지") `
            -Message "Native registry path was not installed"
        Assert-Equal -Expected 1 -Actual $installedUsesKey.GetValue("한컴브릿지") `
            -Message "Native registry enable flag was not installed"
        Assert-Equal -Expected $paths.SecurityDll `
            -Actual $installedAutomationKey.GetValue("FilePathCheckerModule") `
            -Message "Security registry path was not installed"
    }
    finally {
        $installedKey.Dispose()
        $installedUsesKey.Dispose()
        $installedAutomationKey.Dispose()
    }

    $restoreResult = Restore-GsgHwpNative -Paths $paths -ModulesKeyPath $modulesKey `
        -AutomationModulesKeyPath $automationModulesKey
    Assert-True -Condition $restoreResult.Restored -Message "Native restore did not run"
    Assert-Equal -Expected ($originalDll -join ",") `
        -Actual (([System.IO.File]::ReadAllBytes($paths.NativeDll)) -join ",") `
        -Message "Original DLL was not restored"
    Assert-Equal -Expected ($originalSecurityDll -join ",") `
        -Actual (([System.IO.File]::ReadAllBytes($paths.SecurityDll)) -join ",") `
        -Message "Original security DLL was not restored"

    $restoredKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($modulesKey)
    $restoredUsesKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("$modulesKey\Uses")
    $restoredAutomationKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
        $automationModulesKey
    )
    try {
        Assert-Equal -Expected "original-bridge.dll" -Actual $restoredKey.GetValue("한컴브릿지") `
            -Message "Original registry value was not restored"
        Assert-True -Condition (
            $null -eq $restoredUsesKey -or
            $restoredUsesKey.GetValueNames() -notcontains "한컴브릿지"
        ) -Message "Registry value that was originally absent was not removed"
        Assert-Equal -Expected "original-security.dll" `
            -Actual $restoredAutomationKey.GetValue("FilePathCheckerModule") `
            -Message "Original security registry value was not restored"
    }
    finally {
        $restoredKey.Dispose()
        if ($null -ne $restoredUsesKey) {
            $restoredUsesKey.Dispose()
        }
        $restoredAutomationKey.Dispose()
    }

    Assert-True -Condition (Test-Path -LiteralPath $restoreResult.BackupFile) `
        -Message "Recovery backup was not preserved after restore"

    Remove-GsgHwpRuntime -Paths $paths
    Assert-True -Condition (-not (Test-Path -LiteralPath $paths.RuntimeVersionRoot)) `
        -Message "Versioned Python runtime was not removed"
}
finally {
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($registryRoot, $false)
    if (Test-Path -LiteralPath $localAppData) {
        $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        $resolvedTarget = [System.IO.Path]::GetFullPath($localAppData)
        Assert-True -Condition $resolvedTarget.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) `
            -Message "QA cleanup target escaped the temporary directory"
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

Write-Output "PASS: release structure, privacy scan, checksums, isolated .venv, native/security registry and DLL restore, runtime cleanup"
