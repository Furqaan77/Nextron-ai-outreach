<#
.SYNOPSIS
The OQ-A9 spike's throwaway supervisor loop (Story 1.20). Story 1.21's scripts\run.ps1 replaces it.

.DESCRIPTION
Starts the serve command (by default the fake, `uv run --frozen python scripts\spike\fake_serve.py`
on -DataDir) with NEXTRON_ENV=<Env> set for the child only, and restarts it as AD-20 says:

- any exit except the child's own exit 3 restarts it after a backoff that starts at
  -BackoffStartSeconds and doubles up to -BackoffMaxSeconds; a child up -ResetAfterSeconds or
  more resets the backoff;
- the child's own exit 3 (lock held) ends the loop, and the supervisor exits 3;
- a stale heartbeat (<DataDir>\heartbeat) kills the child's whole process tree and restarts it.

Stale is counted in this loop's own polls, not from the file's age: every poll (a relative wait of
-PollSeconds) that sees the same modification time adds -PollSeconds, and any change resets the
count. After the child's first beat the limit is -StaleAfterSeconds; before it, -StartGraceSeconds.
So a PC sleep, or a file left stale by the previous child, never counts as a hang.

Events go to -LogPath as JSON lines (UTF-8, no BOM): supervisor_start, start, start_failed, exit,
stale, kill, backoff, stop, max_restarts and supervisor_error.

Windows PowerShell 5.1. Stdlib .NET only; nothing from nextron, and no Nextron data dir.
#>
[CmdletBinding()]
param(
    [ValidateSet('dev', 'test', 'prod')]
    [string]$Env = 'test',
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'Nextron\spike'),
    [string[]]$ServeCommand,
    [string]$LogPath,
    [ValidateRange(0.01, 86400)][double]$BackoffStartSeconds = 5,
    [ValidateRange(0.01, 86400)][double]$BackoffMaxSeconds = 300,
    [ValidateRange(0.01, 86400)][double]$ResetAfterSeconds = 600,
    [ValidateRange(0.01, 86400)][double]$StaleAfterSeconds = 300,
    [ValidateRange(0.01, 86400)][double]$PollSeconds = 15,
    [ValidateRange(0.01, 86400)][double]$StartGraceSeconds = 900,
    # Tests only: end with exit 0 once this many restarts are used up; -1 restarts forever.
    [ValidateRange(-1, 1000000)][int]$MaxRestarts = -1,
    # The fake's beat interval, used only by the default serve command.
    [ValidateRange(0.01, 86400)][double]$FakeBeatSeconds = 30,
    # Tests only: a path that can't run forces the Process.Kill fallback.
    [string]$TaskkillPath = (Join-Path $env:SystemRoot 'System32\taskkill.exe')
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Invariant = [Globalization.CultureInfo]::InvariantCulture
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false
$EngineRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..')).TrimEnd('\')
$DataDir = [IO.Path]::GetFullPath($DataDir).TrimEnd('\')
if (-not $LogPath) { $LogPath = Join-Path $DataDir 'logs\supervisor.log' }
$LogPath = [IO.Path]::GetFullPath($LogPath)
$HeartbeatPath = Join-Path $DataDir 'heartbeat'

if (-not $ServeCommand) {
    $uv = Get-Command 'uv.exe' -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $uvPath = if ($uv) { $uv.Path } else { 'uv.exe' }
    $ServeCommand = @(
        $uvPath, 'run', '--frozen', 'python', (Join-Path $PSScriptRoot 'fake_serve.py'),
        '--heartbeat', $HeartbeatPath,
        '--control', (Join-Path $DataDir 'spike-control'),
        '--log', (Join-Path $DataDir 'logs\fake_serve.log'),
        '--interval', $FakeBeatSeconds.ToString('R', $Invariant)
    )
}

function Write-SupervisorEvent {
    param([string]$Name, [System.Collections.IDictionary]$Fields = @{})
    $record = [ordered]@{
        ts             = [DateTime]::UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", $Invariant)
        event          = $Name
        supervisor_pid = $PID
    }
    foreach ($key in $Fields.Keys) { $record[$key] = $Fields[$key] }
    $line = (ConvertTo-Json -InputObject $record -Compress -Depth 4) + "`n"
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            [IO.File]::AppendAllText($LogPath, $line, $Utf8NoBom)
            return
        } catch {
            Start-Sleep -Milliseconds 50
        }
    }
}

function Format-Argument {
    # One argument quoted for CommandLineToArgvW, which Python, uv and most programs parse with.
    param([string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]'\') {
            $slashes++
            continue
        }
        if ($character -eq [char]'"') {
            [void]$builder.Append([char]'\', 2 * $slashes + 1)
        } elseif ($slashes -gt 0) {
            [void]$builder.Append([char]'\', $slashes)
        }
        [void]$builder.Append($character)
        $slashes = 0
    }
    if ($slashes -gt 0) { [void]$builder.Append([char]'\', 2 * $slashes) }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Join-Arguments {
    param([string[]]$Arguments)
    $parts = foreach ($argument in $Arguments) { Format-Argument $argument }
    return ($parts -join ' ')
}

function Start-Child {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $ServeCommand[0]
    $info.Arguments = Join-Arguments @($ServeCommand | Select-Object -Skip 1)
    $info.UseShellExecute = $false
    $info.WorkingDirectory = $EngineRoot
    # Set for the child only: this process's own environment is never changed.
    $info.EnvironmentVariables['NEXTRON_ENV'] = $Env
    return [System.Diagnostics.Process]::Start($info)
}

function Get-HeartbeatTicks {
    # The heartbeat's modification time in ticks; a missing file reads as 1601-01-01.
    try {
        return [IO.File]::GetLastWriteTimeUtc($HeartbeatPath).Ticks
    } catch {
        return [long]-1
    }
}

function Invoke-Taskkill {
    param([int]$ProcessId)
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $TaskkillPath
    $info.Arguments = "/PID $ProcessId /T /F"
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($info)
    try {
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        [void]$output.Result
        [void]$errors.Result
        return $process.ExitCode
    } finally {
        $process.Dispose()
    }
}

function Get-Descendants {
    # Every process under ProcessId, deepest last, created no earlier than Since: a process older
    # than the child can only be a reused pid, never one of its descendants.
    param([int]$ProcessId, [DateTime]$Since)
    $all = @(Get-CimInstance -ClassName Win32_Process)
    $found = New-Object System.Collections.Generic.List[int]
    $queue = New-Object System.Collections.Generic.Queue[int]
    $queue.Enqueue($ProcessId)
    while ($queue.Count -gt 0) {
        $parent = $queue.Dequeue()
        foreach ($process in $all) {
            $id = [int]$process.ProcessId
            if ($process.ParentProcessId -eq $parent -and $id -ne $ProcessId -and
                $process.CreationDate -and $process.CreationDate -ge $Since -and
                -not $found.Contains($id)) {
                $found.Add($id)
                $queue.Enqueue($id)
            }
        }
    }
    return , $found.ToArray()
}

function Stop-ChildTree {
    # Kills the child and every process under it (uv, the venv launcher and Python): killing
    # uv.exe alone leaves both Pythons running. If taskkill fails, the fallback kills each
    # descendant itself, deepest first, then the child.
    param([System.Diagnostics.Process]$Process, [string]$Reason)
    $childPid = $Process.Id
    try {
        # Whole microseconds, as Win32_Process.CreationDate has them.
        $since = $Process.StartTime
        $since = $since.AddTicks( - ($since.Ticks % 10))
    } catch {
        $since = [DateTime]::Now  # unknown: match no descendant rather than a stranger
    }
    # ok means the whole tree is gone, not taskkill's exit code: taskkill /T exits 255 when a
    # process of the tree ended by itself while it ran (the venv launcher's job ends its Python),
    # though nothing is left. A failure to list the tree counts as not gone.
    $gone = $false
    $fields = [ordered]@{ pid = $childPid; reason = $Reason; method = 'taskkill' }
    try {
        $fields.code = Invoke-Taskkill $childPid
        if ($Process.WaitForExit(10000)) {
            # Get-Descendants returns one int[] (unary comma), so no @() around it.
            $left = Get-Descendants $childPid $since
            $gone = ($left.Count -eq 0)
        }
    } catch {
        $fields.error = $_.Exception.Message
    }
    $fields.ok = $gone
    Write-SupervisorEvent 'kill' $fields
    if ($gone) { return }
    $errors = @()
    $descendants = @()
    try {
        $descendants = Get-Descendants $childPid $since
    } catch {
        $errors += "listing descendants: $($_.Exception.Message)"
    }
    [array]::Reverse($descendants)
    foreach ($id in $descendants) {
        try {
            Stop-Process -Id $id -Force -ErrorAction Stop
        } catch {
            if (Get-Process -Id $id -ErrorAction SilentlyContinue) { $errors += "${id}: $($_.Exception.Message)" }
        }
    }
    try {
        if (-not $Process.HasExited) { $Process.Kill() }
    } catch {
        if (-not $Process.HasExited) { $errors += "${childPid}: $($_.Exception.Message)" }
    }
    $fields = [ordered]@{
        pid = $childPid; reason = $Reason; method = 'process_kill'; ok = ($errors.Count -eq 0)
        pids = @($descendants + @($childPid))
    }
    if ($errors) { $fields.error = $errors -join '; ' }
    Write-SupervisorEvent 'kill' $fields
}

$child = $null
$exitCode = 0
try {
    [void](New-Item -ItemType Directory -Force -Path $DataDir)
    [void](New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath))
    Write-SupervisorEvent 'supervisor_start' ([ordered]@{
            env             = $Env
            data_dir        = $DataDir
            working_dir     = $EngineRoot
            command         = $ServeCommand
            backoff_start_s = $BackoffStartSeconds
            backoff_max_s   = $BackoffMaxSeconds
            reset_after_s   = $ResetAfterSeconds
            stale_after_s   = $StaleAfterSeconds
            poll_s          = $PollSeconds
            start_grace_s   = $StartGraceSeconds
            max_restarts    = $MaxRestarts
        })
    $pollMilliseconds = [int][Math]::Max(1, [Math]::Round($PollSeconds * 1000))
    $backoffStart = [Math]::Min($BackoffStartSeconds, $BackoffMaxSeconds)
    $backoff = $backoffStart
    $restarts = 0
    $attempt = 0
    while ($true) {
        $attempt++
        $uptime = 0.0
        $lastBeat = Get-HeartbeatTicks
        $clock = [Diagnostics.Stopwatch]::StartNew()
        try {
            $child = Start-Child
        } catch {
            $child = $null
            Write-SupervisorEvent 'start_failed' ([ordered]@{
                    attempt = $attempt; error = $_.Exception.Message
                })
        }
        if ($child) {
            Write-SupervisorEvent 'start' ([ordered]@{ attempt = $attempt; pid = $child.Id })
            $killed = $false
            $firstBeat = $false
            $quietPolls = 0
            # WaitForExit(ms) is a relative wait: time the PC spends asleep is not counted.
            while (-not $child.WaitForExit($pollMilliseconds)) {
                $beat = Get-HeartbeatTicks
                if ($beat -ne $lastBeat) {
                    $lastBeat = $beat
                    $firstBeat = $true
                    $quietPolls = 0
                    continue
                }
                $quietPolls++
                $quiet = $quietPolls * $PollSeconds
                $limit = if ($firstBeat) { $StaleAfterSeconds } else { $StartGraceSeconds }
                if ($quiet -ge $limit) {
                    Write-SupervisorEvent 'stale' ([ordered]@{
                            pid               = $child.Id
                            quiet_s           = [Math]::Round($quiet, 3)
                            limit_s           = $limit
                            before_first_beat = (-not $firstBeat)
                        })
                    Stop-ChildTree $child 'stale'
                    $killed = $true
                    break
                }
            }
            $child.WaitForExit()
            $uptime = $clock.Elapsed.TotalSeconds
            $code = $child.ExitCode
            Write-SupervisorEvent 'exit' ([ordered]@{
                    pid      = $child.Id
                    code     = $code
                    uptime_s = [Math]::Round($uptime, 3)
                    killed   = $killed
                })
            $child.Dispose()
            $child = $null
            if (-not $killed -and $code -eq 3) {
                Write-SupervisorEvent 'stop' ([ordered]@{ code = 3 })
                $exitCode = 3
                break
            }
        }
        if ($MaxRestarts -ge 0 -and $restarts -ge $MaxRestarts) {
            Write-SupervisorEvent 'max_restarts' ([ordered]@{ restarts = $restarts })
            break
        }
        if ($uptime -ge $ResetAfterSeconds) { $backoff = $backoffStart }
        Write-SupervisorEvent 'backoff' ([ordered]@{ wait_s = $backoff })
        Start-Sleep -Milliseconds ([int][Math]::Round($backoff * 1000))
        $backoff = [Math]::Min($backoff * 2, $BackoffMaxSeconds)
        $restarts++
    }
} catch {
    Write-SupervisorEvent 'supervisor_error' ([ordered]@{ error = $_.Exception.Message })
    $exitCode = 1
} finally {
    if ($child -and -not $child.HasExited) { Stop-ChildTree $child 'supervisor_exit' }
}
exit $exitCode
