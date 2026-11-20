# Packs the Nextron build so it can move to another Windows PC.
# Run on the OLD PC, after the build is stopped (see MOVE-TO-NEW-MACHINE.md, step 1).
#
#   powershell -ExecutionPolicy Bypass -File "E:\Nextron AI outreach\_handoff\pack_for_move.ps1" -Dest "F:\NextronMove"
#
# It only READS the project folders and WRITES into -Dest. It changes nothing in the repo.
param(
    [Parameter(Mandatory = $true)][string]$Dest,
    [switch]$Force
)
# 'Continue', because git prints progress on stderr; robocopy failures are caught through its exit code.
$ErrorActionPreference = 'Continue'

$Main = 'E:\Nextron AI outreach'
$Worktrees = @('E:\Nextron-epic1', 'E:\Nextron-epic2', 'E:\Nextron-track3', 'E:\Nextron-track4', 'E:\Nextron-track5')
$MemoryDir = Join-Path $env:USERPROFILE '.claude\projects\e--Nextron-AI-outreach\memory'

# 1. Refuse while the build is still running (copying a moving target gives a broken copy).
$busy = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'Nextron' -and $_.CommandLine -match 'pytest|suite_lock|lint-imports' })
$lock = Test-Path (Join-Path $Main '_handoff\pytest-suite.lock')
if (($busy.Count -gt 0 -or $lock) -and -not $Force) {
    Write-Host "STOP: Nextron tests are still running ($($busy.Count) processes, lock present: $lock)."
    Write-Host "Ask Claude to stop the build first (MOVE-TO-NEW-MACHINE.md step 1), or re-run with -Force."
    exit 1
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null

# 2. Refresh the memory copies inside _handoff so they travel with the project.
if (Test-Path $MemoryDir) {
    Copy-Item -Path (Join-Path $MemoryDir '*.md') -Destination (Join-Path $Main '_handoff\memory') -Force
    Write-Host "Refreshed _handoff\memory from $MemoryDir"
}

# 3. Write a manifest of every worktree's branch, commit and uncommitted files.
$manifest = Join-Path $Dest 'MANIFEST.txt'
"Packed on $(Get-Date -Format 'yyyy-MM-dd HH:mm') from $env:COMPUTERNAME" | Set-Content -Encoding utf8 $manifest
foreach ($wt in @($Main) + $Worktrees) {
    if (-not (Test-Path $wt)) { continue }
    Add-Content -Encoding utf8 $manifest "`n=== $wt"
    Add-Content -Encoding utf8 $manifest (git -C $wt log --oneline -1 --decorate | Out-String)
    Add-Content -Encoding utf8 $manifest (git -C $wt status --short | Out-String)
}

# 4. A git bundle of every branch, as a safety copy of all commits.
git -C $Main bundle create (Join-Path $Dest 'nextron-all-branches.bundle') --all
Write-Host "Wrote git bundle"

# 5. Copy the folders. Skip things the new PC rebuilds itself (virtualenvs, caches, test temp).
$skipDirs = @('.venv', '__pycache__', '.pytest_cache', '.import_linter_cache', '.ruff_cache', '.mypy_cache', 'node_modules', '.wrangler')
foreach ($src in @($Main) + $Worktrees) {
    if (-not (Test-Path $src)) { Write-Host "missing, skipped: $src"; continue }
    $name = Split-Path $src -Leaf
    $dst = Join-Path $Dest $name
    $xd = $skipDirs + @((Join-Path $Main '_handoff\scratch'), (Join-Path $Main '_handoff\pytest-suite.lock'))
    robocopy $src $dst /E /COPY:DAT /R:1 /W:1 /NFL /NDL /NP /XD @xd | Out-Null
    if ($LASTEXITCODE -ge 8) { Write-Host "ROBOCOPY FAILED for $src (code $LASTEXITCODE)"; exit 1 }
    Write-Host "Copied $src -> $dst"
}
New-Item -ItemType Directory -Force -Path (Join-Path $Dest 'Nextron AI outreach\_handoff\scratch') | Out-Null

$size = (Get-ChildItem $Dest -Recurse -Force -File | Measure-Object Length -Sum).Sum / 1GB
Write-Host ("Done. {0:N2} GB in {1}. Next: follow MOVE-TO-NEW-MACHINE.md step 3 on the new PC." -f $size, $Dest)
