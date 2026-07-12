# Claude Code SessionEnd hook - appends an objective session stub to the vault's
# dev journal (90-Meta/sessions/<year>/<date>-<project>.md).
#
# Deterministic by design (invariant D3): plain filesystem append, no model, works
# with Obsidian closed. Must NEVER break a session: every failure exits 0 silently.
#
# Vault root: $env:ARGUS_VAULT overrides the default below.

$ErrorActionPreference = "Stop"

try {
    $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json

    $vault = $env:ARGUS_VAULT
    if (-not $vault) { $vault = "C:\Users\ethan\Documents\Scientia" }
    if (-not (Test-Path $vault)) { exit 0 }

    $cwd = if ($payload.cwd) { $payload.cwd } else { (Get-Location).Path }
    $sessionId = if ($payload.session_id) { $payload.session_id } else { "unknown" }
    $slug = (Split-Path $cwd -Leaf).ToLower()

    $now = Get-Date
    $year = $now.ToString("yyyy")
    $day = $now.ToString("yyyy-MM-dd")
    $time = $now.ToString("HH:mm")

    $sessionDir = Join-Path $vault "90-Meta\sessions\$year"
    if (-not (Test-Path $sessionDir)) {
        New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null
    }
    $notePath = Join-Path $sessionDir "$day-$slug.md"

    # Git context (only when cwd is a repo).
    $branch = ""
    $changedFiles = @()
    try {
        $inRepo = git -C $cwd rev-parse --is-inside-work-tree 2>$null
        if ($inRepo -eq "true") {
            $branch = (git -C $cwd rev-parse --abbrev-ref HEAD 2>$null)
            $changedFiles = @(git -C $cwd diff --name-only HEAD 2>$null | Where-Object { $_ })
        }
    } catch {}

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    if (-not (Test-Path $notePath)) {
        $header = @"
---
type: dev-session
project: $slug
date: $day
tags: [dev-journal]
---

# $day - $slug

> Auto-created by the Claude Code session-end hook. Stubs are script-written;
> narratives are added on demand via /log-session.
"@
        [System.IO.File]::WriteAllText($notePath, $header + "`n", $utf8NoBom)
    }

    $idPrefix = $sessionId.Substring(0, [Math]::Min(8, $sessionId.Length))
    $lines = @("", "## $time - session $idPrefix", "")
    $lines += "- **project:** $slug"
    $lines += "- **cwd:** ``$cwd``"
    $lines += "- **session_id:** ``$sessionId``"
    if ($branch) {
        $lines += "- **branch:** ``$branch``"
        $lines += "- **files changed:** $($changedFiles.Count)"
        foreach ($file in ($changedFiles | Select-Object -First 20)) {
            $lines += "    - ``$file``"
        }
        if ($changedFiles.Count -gt 20) {
            $lines += "    - ... and $($changedFiles.Count - 20) more"
        }
    }

    [System.IO.File]::AppendAllText($notePath, (($lines -join "`n") + "`n"), $utf8NoBom)
} catch {
    # Journaling must never break a session.
}
exit 0
