# Skill registration ownership and migration

Read this only when an installation exposes the skill twice, or exposes it from an unexpected path.

The plugin manifest (`.codex-plugin/plugin.json` with `"skills": "./skills/"`) is the normal and only active owner of this skill. MCP worker startup reports manifest and legacy-path status but never creates, replaces, or removes anything under the global Codex skills directory.

The host may expose this skill directly from the plugin manifest, from a host cache, or through a legacy global Junction. The displayed `SKILL.md` path is the active one; other cache or installation paths on disk are stale copies, not evidence about which one the host loaded.

For an existing installation, migrate in this order:

1. Confirm the installed plugin exposes `automate-hancom-documents` from its manifest. A missing global Junction is the expected state.
2. Inspect the old path without changing it: `Get-Item -LiteralPath (Join-Path $codexHome "skills\automate-hancom-documents") -Force | Select-Object LinkType, Target`, where `$codexHome` is the configured `CODEX_HOME` or the user `.codex` directory.
3. Remove that path only when it is a Junction and its resolved target is exactly this plugin's `skills\automate-hancom-documents` directory. Leave a real directory, an unreadable path, or a Junction to another installation untouched.
4. Reconnect the plugin and confirm the host exposes one skill entry.

From the installed plugin root, this PowerShell guard removes only the current plugin's own Junction and stops on every other path:

```powershell
$codexHome = if ($env:CODEX_HOME) { [IO.Path]::GetFullPath($env:CODEX_HOME) } else { Join-Path $env:USERPROFILE ".codex" }
$legacySkill = Join-Path $codexHome "skills\automate-hancom-documents"
$currentSkill = (Resolve-Path ".\skills\automate-hancom-documents").Path
$item = Get-Item -LiteralPath $legacySkill -Force
$junctionTarget = [IO.Path]::GetFullPath([string]$item.Target)
if ($item.LinkType -ne "Junction" -or $junctionTarget -ne $currentSkill) { throw "Refusing to remove a path not owned by this plugin" }
Remove-Item -LiteralPath $legacySkill
```

Only a legacy host that cannot discover manifest skills should install or repair the compatibility Junction explicitly with `uv run python .\skills\automate-hancom-documents\scripts\hwp_codex_skill_install.py`. The command accepts an absent path or the already-current Junction and refuses every other existing path. It is never called by MCP worker startup.
