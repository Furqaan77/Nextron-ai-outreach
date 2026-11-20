# Sets up the copied Nextron build on the NEW PC and checks that it works.
# Run after copying the folders (MOVE-TO-NEW-MACHINE.md step 3):
#
#   powershell -ExecutionPolicy Bypass -File "E:\Nextron AI outreach\_handoff\setup_new_machine.ps1"
#
# If you put the folders somewhere other than E:\, pass the folder that holds them:
#   ... setup_new_machine.ps1 -Parent "D:\Projects"
#
# What it changes: git's worktree links, the .venv folders (rebuilt), the paths inside _handoff
# (only if -Parent is not E:\) and Claude's memory folder for this project. Nothing else.
param([string]$Parent = 'E:\')
# 'Continue', because git and uv print progress on stderr; failures are caught through exit codes.
$ErrorActionPreference = 'Continue'

$Parent = $Parent.TrimEnd('\') + '\'
$Main = Join-Path $Parent 'Nextron AI outreach'
$Names = @('Nextron-epic1', 'Nextron-epic2', 'Nextron-track3', 'Nextron-track4', 'Nextron-track5')
$Worktrees = @($Names | ForEach-Object { Join-Path $Parent $_ } | Where-Object { Test-Path $_ })
$problems = 0

function Test-Tool($name, $hint) {
    if (Get-Command $name -ErrorAction SilentlyContinue) {
        Write-Host ("ok   {0}: {1}" -f $name, ((& $name --version) | Select-Object -First 1))
        return $true
    }
    Write-Host "MISSING $name - $hint"
    $script:problems++
    return $false
}

Write-Host '--- 1. Tools'
$hasGit = Test-Tool 'git' 'install Git for Windows: https://git-scm.com/download/win'
$hasUv = Test-Tool 'uv' 'install uv: https://docs.astral.sh/uv/getting-started/installation/'
if (-not ($hasGit -and $hasUv)) { Write-Host 'Install the missing tools, open a NEW terminal, then run this again.'; exit 1 }
if (-not (Test-Path $Main)) { Write-Host "MISSING $Main - copy the folders first (step 3)."; exit 1 }

Write-Host '--- 2. Python 3.14 (managed by uv)'
uv python install 3.14

Write-Host '--- 3. Re-link the git worktrees to the main repo'
git -C $Main worktree repair @Worktrees
git -C $Main worktree prune
git -C $Main worktree list

Write-Host '--- 4. Point _handoff paths at the new location (only if it moved off E:\)'
if ($Parent -ne 'E:\') {
    $new = $Parent.TrimEnd('\')
    Get-ChildItem (Join-Path $Main '_handoff') -File -Include *.md, *.py, *.ps1 -Recurse |
        Where-Object { $_.FullName -notmatch '\\scratch\\' } | ForEach-Object {
            $t = [IO.File]::ReadAllText($_.FullName)
            $u = [regex]::Replace($t, '(?i)\be:[\\/](?=Nextron)', ($new.Replace('$', '$$') + '\'))
            if ($u -ne $t) { [IO.File]::WriteAllText($_.FullName, $u); Write-Host "retargeted $($_.Name)" }
        }
}

Write-Host '--- 5. Rebuild each virtualenv (uv sync --frozen)'
foreach ($wt in @($Main) + $Worktrees) {
    $eng = Join-Path $wt 'nextron-engine'
    if (-not (Test-Path $eng)) { continue }
    Push-Location $eng
    uv sync --frozen
    if ($LASTEXITCODE -eq 0) { Write-Host "ok   venv $eng" } else { Write-Host "FAILED uv sync in $eng"; $problems++ }
    Pop-Location
}

Write-Host "--- 6. Restore Claude's memory notes for this project"
$key = ($Main -replace '[^A-Za-z0-9]', '-')
$memDir = Join-Path $env:USERPROFILE ".claude\projects\$key\memory"
New-Item -ItemType Directory -Force -Path $memDir | Out-Null
Get-ChildItem (Join-Path $Main '_handoff\memory') -Filter *.md | ForEach-Object {
    $target = Join-Path $memDir $_.Name
    if (-not (Test-Path $target)) { Copy-Item $_.FullName $target; Write-Host "restored memory $($_.Name)" }
}

Write-Host '--- 7. Quick health check on main (import contracts, about a minute)'
Push-Location (Join-Path $Main 'nextron-engine')
try {
    uv run lint-imports
    if ($LASTEXITCODE -ne 0) { $problems++ }
} finally { Pop-Location }

Write-Host '--- 8. Uncommitted work carried over (resume these stories)'
foreach ($wt in @($Main) + $Worktrees) {
    $n = @(git -C $wt status --short).Count
    Write-Host ("{0,-28} {1}  dirty files: {2}" -f (Split-Path $wt -Leaf), (git -C $wt branch --show-current), $n)
}

if ($problems -eq 0) {
    Write-Host "`nREADY. Open VS Code at '$Main', sign in to Claude Code, and say:"
    Write-Host '  "We moved to a new PC. Read _handoff/MOVE-TO-NEW-MACHINE.md and _handoff/START-HERE.md, then continue the build."'
} else {
    Write-Host "`n$problems problem(s) above. Fix them and run this script again (it is safe to re-run)."
}
