# Argus dev-loop setup - installs the Claude Code session-journal hooks and
# /log-session command at user scope (~/.claude/), and merges the hook wiring
# into ~/.claude/settings.json (preserving everything already there).
#
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File claude-integration\setup.ps1
# Optional:                -VaultPath "D:\path\to\your\vault"   (default: C:\Users\<you>\Documents\Scientia)

param(
    [string]$VaultPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
$claudeDir = Join-Path $env:USERPROFILE ".claude"

# 1. Install hook scripts + command.
$hooksDir = Join-Path $claudeDir "hooks"
$commandsDir = Join-Path $claudeDir "commands"
New-Item -ItemType Directory -Force -Path $hooksDir, $commandsDir | Out-Null
Copy-Item (Join-Path $PSScriptRoot "hooks\*.ps1") $hooksDir -Force
Copy-Item (Join-Path $PSScriptRoot "commands\log-session.md") $commandsDir -Force
Write-Host "Installed hooks -> $hooksDir"
Write-Host "Installed /log-session -> $commandsDir"

# 2. Merge hook wiring into settings.json (non-destructive).
$settingsPath = Join-Path $claudeDir "settings.json"
$snippet = Get-Content (Join-Path $PSScriptRoot "settings-hooks.snippet.json") -Raw | ConvertFrom-Json

if (Test-Path $settingsPath) {
    Copy-Item $settingsPath "$settingsPath.bak" -Force
    $settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
} else {
    $settings = New-Object PSObject
}

if (-not ($settings.PSObject.Properties.Name -contains "hooks")) {
    $settings | Add-Member -MemberType NoteProperty -Name "hooks" -Value (New-Object PSObject)
}
foreach ($eventName in @("SessionStart", "SessionEnd")) {
    if ($settings.hooks.PSObject.Properties.Name -contains $eventName) {
        Write-Host "settings.json already has a $eventName hook - left untouched." -ForegroundColor Yellow
    } else {
        $settings.hooks | Add-Member -MemberType NoteProperty -Name $eventName -Value $snippet.hooks.$eventName
        Write-Host "Wired $eventName hook into settings.json"
    }
}

$settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding utf8
Write-Host "Updated $settingsPath (backup at settings.json.bak)"

# 3. Optional: record a non-default vault path for the hooks.
if ($VaultPath) {
    [Environment]::SetEnvironmentVariable("ARGUS_VAULT", $VaultPath, "User")
    Write-Host "Set user env ARGUS_VAULT=$VaultPath (hooks pick it up on next session)"
}

Write-Host ""
Write-Host "Done. Restart Claude Code (or open /hooks once) to activate the hooks." -ForegroundColor Green
