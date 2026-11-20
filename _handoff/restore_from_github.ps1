# Rebuilds the whole Nextron build on a NEW PC from GitHub: main, every track worktree,
# the half-built stories and the _handoff context folder.
#
# 1. Install Git for Windows and uv (see MOVE-TO-NEW-MACHINE.md section 0).
# 2. Download this one script and run it in PowerShell:
#      powershell -ExecutionPolicy Bypass -File restore_from_github.ps1
#    or pass a different parent folder:  ... -Parent "D:\Projects"
# 3. It then runs setup_new_machine.ps1 for you.
param([string]$Parent = 'E:\')
$ErrorActionPreference = 'Continue'
$Repo = 'https://github.com/Furqaan77/Nextron-ai-outreach.git'
$Parent = $Parent.TrimEnd('\') + '\'
$Main = Join-Path $Parent 'Nextron AI outreach'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Host 'Install Git for Windows first.'; exit 1 }
if (Test-Path $Main) { Write-Host "$Main already exists. Move it away or use -Parent."; exit 1 }

# 1. Clone main and fetch every branch.
git clone $Repo $Main
if ($LASTEXITCODE -ne 0) { Write-Host 'clone failed'; exit 1 }
git -C $Main fetch origin '+refs/heads/*:refs/remotes/origin/*'

# 2. The _handoff context folder lives on the 'handoff' branch; unpack it, keep it untracked.
$tarFile = Join-Path $env:TEMP 'nextron-handoff.tar'
git -C $Main archive --format=tar -o $tarFile origin/handoff
tar -xf $tarFile -C $Main
Remove-Item $tarFile
Add-Content -Path (Join-Path $Main '.git\info\exclude') -Value '_handoff/'
New-Item -ItemType Directory -Force -Path (Join-Path $Main '_handoff\scratch') | Out-Null
Write-Host 'Unpacked _handoff'

# 3. Recreate the track worktrees on their branches, then lay the half-built work on top
#    (it comes back as uncommitted changes, exactly as it was on the old PC).
$tracks = @(
    @{ Dir = 'Nextron-epic1';  Branch = 'track-a11'; Wip = $true  },
    @{ Dir = 'Nextron-epic2';  Branch = 'track-b18'; Wip = $true  },
    @{ Dir = 'Nextron-track3'; Branch = 'track-c4';  Wip = $false },
    @{ Dir = 'Nextron-track4'; Branch = 'track-d13'; Wip = $true  },
    @{ Dir = 'Nextron-track5'; Branch = 'track-e16'; Wip = $true  }
)
foreach ($t in $tracks) {
    $wt = Join-Path $Parent $t.Dir
    git -C $Main worktree add $wt -b $t.Branch "origin/$($t.Branch)"
    if ($LASTEXITCODE -ne 0) { Write-Host "worktree add failed for $($t.Dir)"; continue }
    if ($t.Wip) {
        git -C $wt restore --source="origin/wip/$($t.Branch)" --worktree -- .
        Write-Host ("{0}: restored {1} uncommitted files" -f $t.Dir, @(git -C $wt status --short).Count)
    }
}
# Story 6.2's finished commit sits on track-b16 (merged by hash later); keep a local branch for it.
git -C $Main branch track-b16 origin/track-b16

# 4. Tools, Python, venvs, Claude memory, health check.
& (Join-Path $Main '_handoff\setup_new_machine.ps1') -Parent $Parent
