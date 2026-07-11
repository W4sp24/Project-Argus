# Claude Code SessionStart hook - if the vault has a project context note matching
# the current working directory (90-Meta/projects/<cwd-basename>.md), inject it as
# additionalContext so Claude resumes grounded in prior decisions.
#
# Deterministic file read, no model, works offline. Failures exit 0 silently.
#
# Vault root: $env:FRIDAY_VAULT overrides the default below.

$ErrorActionPreference = "Stop"

try {
    $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json

    $vault = $env:FRIDAY_VAULT
    if (-not $vault) { $vault = "C:\Users\ethan\Documents\Scientia" }

    $cwd = if ($payload.cwd) { $payload.cwd } else { (Get-Location).Path }
    $slug = (Split-Path $cwd -Leaf).ToLower()
    $notePath = Join-Path $vault "90-Meta\projects\$slug.md"

    if (Test-Path $notePath) {
        $content = [System.IO.File]::ReadAllText($notePath)
        $context = "Project context note from the Scientia vault " +
            "(90-Meta/projects/$slug.md - dev journal, see /log-session):`n`n$content"
        $result = @{
            hookSpecificOutput = @{
                hookEventName     = "SessionStart"
                additionalContext = $context
            }
        }
        Write-Output ($result | ConvertTo-Json -Depth 4)
    }
} catch {
    # Context injection must never break a session.
}
exit 0
