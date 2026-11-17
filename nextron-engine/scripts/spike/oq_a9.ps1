<#
.SYNOPSIS
The OQ-A9 spike's control script (Story 1.20): prints, registers, drives, inspects and removes the
throwaway logon task that runs scripts\spike\supervisor.ps1. Story 1.21 replaces the spike.

.DESCRIPTION
Run it from a normal (not elevated) Windows PowerShell 5.1, in the nextron-engine folder:

  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\spike\oq_a9.ps1 [-Action <action>]

Actions:
  DryRun      (default) print the trigger, principal, settings and action; register nothing.
  Register    register the AtLogOn task -TaskName for the current user (it starts at the next
              logon) and print Get-ScheduledTask and Get-ScheduledTaskInfo.
  Status      print the task, its last result, the heartbeat's age, the supervisor log's last
              events and the spike's processes.
  Kill        kill the fake's Python (a crash): the supervisor restarts it after its backoff.
  Stall       tell the running fake to stop beating: the supervisor kills it after 5 minutes.
  Exit3       tell the running fake to exit with code 3: the supervisor stops looping.
  Unregister  stop and remove the task -TaskName and any spike process left on -DataDir.
  Prove       the contained proof: register an on-demand NextronSpikeTest-<hex> task (no
              trigger) on a fresh temp folder, run every case through Task Scheduler, write
              <proof dir>\results.json, and always remove the task and its processes.

It refuses (one line on stderr, exit 2) to run elevated, or with -TaskName set to the
production task's name in any letter case, or to a name with wildcard or path characters.
Kill, Stall and Exit3 touch only -DataDir; no action ever touches the production task.
#>
[CmdletBinding()]
param(
    [ValidateSet('DryRun', 'Register', 'Status', 'Kill', 'Stall', 'Exit3', 'Unregister', 'Prove')]
    [string]$Action = 'DryRun',
    [string]$TaskName = 'Nextron Spike OQ-A9',
    [ValidateSet('dev', 'test', 'prod')]
    [string]$Env = 'test',
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'Nextron\spike')
)

$ErrorActionPreference = 'Stop'

$ProdTaskName = 'Nextron Engine (prod)'
$ProofPrefix = 'NextronSpikeTest-'
$Invariant = [Globalization.CultureInfo]::InvariantCulture
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false
$EngineRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..')).TrimEnd('\')
$SupervisorPath = Join-Path $PSScriptRoot 'supervisor.ps1'
$PowerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$DataDir = [IO.Path]::GetFullPath($DataDir).TrimEnd('\')
$Me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Description = 'Nextron OQ-A9 spike (Story 1.20). Throwaway: oq_a9.ps1 -Action Unregister removes it.'

# The contained proof's timings, seconds. Mac's run uses the supervisor's defaults, the AD-20
# values: backoff 5 doubling to 300, reset after 600 up, stale at 300, poll 15, start grace 900.
$ProofTimings = [ordered]@{
    BackoffStartSeconds = 2
    BackoffMaxSeconds   = 8
    ResetAfterSeconds   = 600
    StaleAfterSeconds   = 6
    PollSeconds         = 1
    StartGraceSeconds   = 120
    FakeBeatSeconds     = 1
}

# --- Refusals ------------------------------------------------------------------------------------

function Test-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Refusal {
    if (Test-Elevated) {
        return 'this PowerShell is elevated; run it from a normal one (an elevated run proves nothing for OQ-A9)'
    }
    $name = $TaskName.Trim()
    if ($name -ieq $ProdTaskName) {
        return "'$ProdTaskName' is the production task; the spike never touches it"
    }
    if (-not $name -or $name.IndexOfAny([char[]]'*?[]\/') -ge 0) {
        return "-TaskName must be a plain name with no wildcard or path characters: '$TaskName'"
    }
    return $null
}

# --- The AD-20 task, built once and shared by every action ---------------------------------------

function Get-SupervisorArguments {
    param([string]$Dir, [System.Collections.IDictionary]$Timings)
    # Windows paths can't hold a double quote, and $Dir has no trailing backslash.
    $parts = @(
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
        '-File', ('"' + $SupervisorPath + '"'), '-Env', $Env, '-DataDir', ('"' + $Dir + '"')
    )
    foreach ($key in $Timings.Keys) {
        $parts += "-$key"
        $parts += ([double]$Timings[$key]).ToString('R', $Invariant)
    }
    return ($parts -join ' ')
}

function New-SpikeTaskParts {
    param([string]$Dir, [System.Collections.IDictionary]$Timings = @{}, [switch]$OnDemand)
    $trigger = $null
    if (-not $OnDemand) { $trigger = New-ScheduledTaskTrigger -AtLogOn -User $Me }
    $arguments = Get-SupervisorArguments $Dir $Timings
    return [ordered]@{
        Trigger   = $trigger
        Principal = New-ScheduledTaskPrincipal -UserId $Me -LogonType Interactive -RunLevel Limited
        Settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -MultipleInstances IgnoreNew
        Action    = New-ScheduledTaskAction -Execute $PowerShellPath -Argument $arguments `
            -WorkingDirectory $EngineRoot
    }
}

function New-SpikeTask {
    param([System.Collections.IDictionary]$Parts)
    if ($Parts.Trigger) {
        return New-ScheduledTask -Trigger $Parts.Trigger -Principal $Parts.Principal `
            -Settings $Parts.Settings -Action $Parts.Action -Description $Description
    }
    return New-ScheduledTask -Principal $Parts.Principal -Settings $Parts.Settings `
        -Action $Parts.Action -Description $Description
}

function Format-TaskParts {
    param([System.Collections.IDictionary]$Parts, [string]$Name)
    $trigger = $Parts.Trigger
    $principal = $Parts.Principal
    $settings = $Parts.Settings
    $action = $Parts.Action
    $triggerText = 'none (started on demand only)'
    if ($trigger) { $triggerText = "AtLogOn ($($trigger.CimClass.CimClassName)), user $($trigger.UserId)" }
    return @(
        "Task name   : $Name"
        "Trigger     : $triggerText"
        "Principal   : UserId $($principal.UserId), LogonType $($principal.LogonType), RunLevel $($principal.RunLevel)"
        ("Settings    : ExecutionTimeLimit $($settings.ExecutionTimeLimit), " +
            "DisallowStartIfOnBatteries $($settings.DisallowStartIfOnBatteries), " +
            "StopIfGoingOnBatteries $($settings.StopIfGoingOnBatteries), " +
            "StartWhenAvailable $($settings.StartWhenAvailable), " +
            "MultipleInstances $($settings.MultipleInstances), Priority $($settings.Priority)")
        "Action      : $($action.Execute) $($action.Arguments)"
        "Working dir : $($action.WorkingDirectory)"
    )
}

function Format-TaskEvidence {
    # Get-ScheduledTask and Get-ScheduledTaskInfo for one task, as text.
    param([string]$Name)
    $task = Get-ScheduledTask -TaskName $Name
    $info = Get-ScheduledTaskInfo -TaskName $Name
    $trigger = @{ Name = 'Trigger'; Expression = { $_.CimClass.CimClassName } }
    $text = @(
        '--- Get-ScheduledTask'
        ($task | Format-List TaskName, TaskPath, State, Author, Description | Out-String -Width 300).Trim()
        ($task.Triggers | Format-List $trigger, UserId, Enabled, Delay | Out-String -Width 300).Trim()
        ($task.Principal | Format-List UserId, LogonType, RunLevel | Out-String -Width 300).Trim()
        ($task.Settings | Format-List ExecutionTimeLimit, DisallowStartIfOnBatteries,
            StopIfGoingOnBatteries, StartWhenAvailable, MultipleInstances, Priority, Hidden |
            Out-String -Width 300).Trim()
        ($task.Actions | Format-List Execute, Arguments, WorkingDirectory | Out-String -Width 300).Trim()
        '--- Get-ScheduledTaskInfo'
        ($info | Format-List LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns |
            Out-String -Width 300).Trim()
    )
    return ($text | Where-Object { $_ }) -join [Environment]::NewLine
}

# --- Logs, control file and processes ------------------------------------------------------------

function Read-JsonLines {
    # Every complete JSON line of a log another process may be writing; nothing if it is missing.
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    $stream = New-Object IO.FileStream($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, $share)
    try {
        $reader = New-Object IO.StreamReader($stream, $Utf8NoBom)
        $text = $reader.ReadToEnd()
    } finally {
        $stream.Dispose()
    }
    foreach ($line in ($text -split "`n")) {
        $line = $line.Trim()
        if (-not $line) { continue }
        try { $line | ConvertFrom-Json } catch { }
    }
}

function Get-Events {
    param([string]$Path, [string]$Name)
    return @(Read-JsonLines $Path | Where-Object { $_.event -eq $Name })
}

function Wait-ForEvent {
    # The Count-th event called Name in the log at Path, waiting up to TimeoutSeconds for it.
    param([string]$Path, [string]$Name, [int]$Count, [double]$TimeoutSeconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ($true) {
        $found = @(Get-Events $Path $Name)
        if ($found.Count -ge $Count) { return $found[$Count - 1] }
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "timed out after $TimeoutSeconds s waiting for '$Name' number $Count in $(Split-Path -Leaf $Path)"
        }
        Start-Sleep -Milliseconds 250
    }
}

function Wait-Until {
    param([scriptblock]$Condition, [double]$TimeoutSeconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while (-not (& $Condition)) {
        if ([DateTime]::UtcNow -ge $deadline) { return $false }
        Start-Sleep -Milliseconds 250
    }
    return $true
}

function Get-Seconds {
    # Seconds from one log timestamp to another.
    param($From, $To)
    $styles = [Globalization.DateTimeStyles]'AdjustToUniversal, AssumeUniversal'
    $start = if ($From -is [DateTime]) { $From } else { [DateTime]::Parse($From, $Invariant, $styles) }
    $end = if ($To -is [DateTime]) { $To } else { [DateTime]::Parse($To, $Invariant, $styles) }
    return [Math]::Round(($end - $start).TotalSeconds, 3)
}

function Send-SpikeCommand {
    # Writes a one-shot command for the fake; the child that reads it deletes it.
    param([string]$Dir, [string]$Text)
    [void](New-Item -ItemType Directory -Force -Path $Dir)
    $target = Join-Path $Dir 'spike-control'
    $temporary = "$target.$PID.tmp"
    [IO.File]::WriteAllText($temporary, $Text, $Utf8NoBom)
    try { [IO.File]::Delete($target) } catch { }
    [IO.File]::Move($temporary, $target)
}

function Get-SpikeProcesses {
    # The spike's processes on Dir: the supervisor, uv and the Pythons. Only those images, and
    # only when the command line names both Dir and a spike script, so an editor or a shell that
    # merely mentions the folder is never listed (and never stopped).
    param([string]$Dir)
    $images = @('powershell.exe', 'uv.exe', 'python.exe', 'pythonw.exe')
    $ignore = [StringComparison]::OrdinalIgnoreCase
    return @(Get-CimInstance -ClassName Win32_Process | Where-Object {
            $_.ProcessId -ne $PID -and $_.CommandLine -and $images -contains $_.Name.ToLowerInvariant() -and
            $_.CommandLine.IndexOf($Dir, $ignore) -ge 0 -and
            ($_.CommandLine.IndexOf('supervisor.ps1', $ignore) -ge 0 -or
                $_.CommandLine.IndexOf('fake_serve.py', $ignore) -ge 0)
        })
}

function Stop-SpikeProcesses {
    param([string]$Dir)
    $stopped = @()
    foreach ($process in (Get-SpikeProcesses $Dir)) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        $stopped += [int]$process.ProcessId
    }
    return , $stopped
}

function Test-Running {
    param([int]$ProcessId)
    return [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-Field {
    param($Object, [string]$Name)
    $property = $Object.PSObject.Properties[$Name]
    if ($property) { return $property.Value }
    return $null
}

# --- Mac's actions --------------------------------------------------------------------------------

function Write-Evidence {
    # Prints the lines and appends them to <DataDir>\oq-a9-evidence.txt for the decision record.
    param([string[]]$Lines)
    [void](New-Item -ItemType Directory -Force -Path $DataDir)
    $stamp = [DateTime]::UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", $Invariant)
    $header = "===== $stamp oq_a9.ps1 -Action $Action -TaskName '$TaskName'"
    $text = (@($header) + $Lines) -join [Environment]::NewLine
    [IO.File]::AppendAllText((Join-Path $DataDir 'oq-a9-evidence.txt'), $text + [Environment]::NewLine, $Utf8NoBom)
    foreach ($line in $Lines) { Write-Output $line }
}

function Invoke-DryRun {
    $parts = New-SpikeTaskParts -Dir $DataDir
    Write-Output 'OQ-A9 spike: dry run. Nothing is registered.'
    Format-TaskParts $parts $TaskName | Write-Output
}

function Invoke-Register {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        [Console]::Error.WriteLine("oq_a9: a task named '$TaskName' already exists; run -Action Unregister first")
        $script:ExitCode = 1
        return
    }
    $parts = New-SpikeTaskParts -Dir $DataDir
    [void](New-Item -ItemType Directory -Force -Path $DataDir)
    $leftover = Remove-LeftoverCommand
    try {
        [void](Register-ScheduledTask -TaskName $TaskName -InputObject (New-SpikeTask $parts))
    } catch {
        Write-Evidence (@(Format-TaskParts $parts $TaskName) + @(
                $leftover, "Registration FAILED without elevation: $($_.Exception.Message)",
                'See docs\spikes\oq-a9-logon-task.md, "Fallbacks".'))
        $script:ExitCode = 1
        return
    }
    Write-Evidence (@(Format-TaskParts $parts $TaskName) + @(
            $leftover, 'Registered without elevation.', (Format-TaskEvidence $TaskName),
            'Next: sign out and sign in again, then run -Action Status.'))
}

function Invoke-Status {
    $lines = @()
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        $lines += Format-TaskEvidence $TaskName
    } else {
        $lines += "No task named '$TaskName' is registered."
    }
    $heartbeat = Join-Path $DataDir 'heartbeat'
    if (Test-Path -LiteralPath $heartbeat) {
        $age = ([DateTime]::UtcNow - [IO.File]::GetLastWriteTimeUtc($heartbeat)).TotalSeconds
        $lines += "--- Heartbeat: last beat $([Math]::Round($age, 1)) s ago"
    } else {
        $lines += '--- Heartbeat: no file yet'
    }
    $lines += '--- Supervisor log, last 15 events'
    $events = @(Read-JsonLines (Join-Path $DataDir 'logs\supervisor.log'))
    $lines += @($events | Select-Object -Last 15 | ForEach-Object { ConvertTo-Json $_ -Compress })
    $lines += '--- Spike processes'
    $lines += (Get-SpikeProcesses $DataDir | Format-Table ProcessId, ParentProcessId, Name,
        CreationDate -AutoSize | Out-String -Width 300).Trim()
    Write-Evidence $lines
}

function Remove-LeftoverCommand {
    # A Stall or Exit3 sent while no fake ran waits in the file, and the next fake would obey it
    # at once: Register and Unregister delete it. Returns the evidence line.
    $control = Join-Path $DataDir 'spike-control'
    if (Test-Path -LiteralPath $control) {
        Remove-Item -LiteralPath $control -Force
        return "Deleted a leftover command file, $control."
    }
    return 'No leftover command file.'
}

function Invoke-Kill {
    $start = Get-Events (Join-Path $DataDir 'logs\fake_serve.log') 'start' | Select-Object -Last 1
    if (-not $start) {
        [Console]::Error.WriteLine("oq_a9: no fake has started on $DataDir yet")
        $script:ExitCode = 1
        return
    }
    $alive = Get-SpikeProcesses $DataDir | Where-Object { $_.ProcessId -eq $start.pid }
    if (-not $alive) {
        [Console]::Error.WriteLine("oq_a9: the last fake (pid $($start.pid)) is not running")
        $script:ExitCode = 1
        return
    }
    Stop-Process -Id $start.pid -Force
    Write-Evidence @("Killed the fake's Python, pid $($start.pid). The supervisor restarts it after its backoff; run -Action Status in a minute.")
}

function Invoke-Control {
    param([string]$Text, [string]$Then)
    Send-SpikeCommand $DataDir $Text
    Write-Evidence @("Sent '$Text' to the fake on $DataDir. $Then")
}

function Invoke-Unregister {
    $lines = @()
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and "$($task.Description)" -ne $Description) {
        # Not one this script registered (or several share the name): never remove it.
        [Console]::Error.WriteLine("oq_a9: refused: the task '$TaskName' was not registered by this spike; nothing was changed")
        $script:ExitCode = 2
        return
    }
    if ($task) {
        if ("$($task.State)" -eq 'Running') {
            try {
                Stop-ScheduledTask -TaskName $TaskName
            } catch {
                $lines += "Stop-ScheduledTask failed: $($_.Exception.Message)"
            }
        }
        try {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            $lines += "Unregistered '$TaskName'."
        } catch {
            $lines += "Unregister-ScheduledTask FAILED: $($_.Exception.Message)"
            $script:ExitCode = 1
        }
    } else {
        $lines += "No task named '$TaskName' is registered."
    }
    $lines += Remove-LeftoverCommand
    $stopped = Stop-SpikeProcesses $DataDir
    $lines += "Stopped $($stopped.Count) leftover spike process(es) on ${DataDir}: $($stopped -join ', ')"
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        $lines += "WARNING: '$TaskName' is still registered."
    } else {
        $lines += "Checked: no task named '$TaskName' remains."
    }
    Write-Evidence $lines
}

# --- The contained proof --------------------------------------------------------------------------

function Test-Denied {
    param($Exception)
    for ($inner = $Exception; $inner; $inner = $inner.InnerException) {
        if ($inner -is [UnauthorizedAccessException]) { return $true }
        if ($inner.HResult -eq -2147024891) { return $true }  # 0x80070005 E_ACCESSDENIED
        if ($inner.Message -match 'denied') { return $true }
    }
    return $false
}

function Invoke-Case {
    # Runs one case: Body gets the case and fills case.details, and throws when the case fails.
    param([string]$CaseName, [scriptblock]$Body)
    $case = [ordered]@{ name = $CaseName; status = 'pass'; details = [ordered]@{} }
    [void]$script:Cases.Add($case)
    if (-not $script:TaskReady) {
        $case.status = 'skipped'
        $case.details.reason = 'the proof task was not registered'
        Write-Output ('{0,-12} {1}' -f $CaseName, $case.status)
        return
    }
    try {
        $null = & $Body $case
    } catch {
        $case.status = if (Test-Denied $_.Exception) { 'denied' } else { 'fail' }
        $case.details.error = $_.Exception.Message
    }
    Write-Output ('{0,-12} {1}' -f $CaseName, $case.status)
}

function Invoke-Prove {
    $hex = [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $proofTask = "$ProofPrefix$hex"
    $proofDir = Join-Path ([IO.Path]::GetTempPath()) "nextron-oq-a9-$hex"
    $proofDir = [IO.Path]::GetFullPath($proofDir).TrimEnd('\')
    [void](New-Item -ItemType Directory -Force -Path $proofDir)
    $supervisorLog = Join-Path $proofDir 'logs\supervisor.log'
    $fakeLog = Join-Path $proofDir 'logs\fake_serve.log'
    $script:Cases = New-Object System.Collections.ArrayList
    $script:TaskReady = $true
    $results = [ordered]@{
        task       = $proofTask
        proof_dir  = $proofDir
        started_at = [DateTime]::UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", $Invariant)
        user       = $Me
        elevated   = $false
        powershell = $PSVersionTable.PSVersion.ToString()
        os         = [Environment]::OSVersion.VersionString
        timings_s  = $ProofTimings
        cases      = $script:Cases
    }
    Write-Output "OQ-A9 contained proof: task $proofTask, folder $proofDir"
    try {
        Invoke-Case 'registration' {
            param($case)
            $script:TaskReady = $false
            $parts = New-SpikeTaskParts -Dir $proofDir -Timings $ProofTimings -OnDemand
            $case.details.dry_run = @(Format-TaskParts $parts $proofTask)
            [void](Register-ScheduledTask -TaskName $proofTask -InputObject (New-SpikeTask $parts))
            $script:TaskReady = $true
            $task = Get-ScheduledTask -TaskName $proofTask
            $case.details.evidence = Format-TaskEvidence $proofTask
            $case.details.xml = (Export-ScheduledTask -TaskName $proofTask | Out-String).Trim()
            $checks = [ordered]@{
                LogonType                  = @("$($task.Principal.LogonType)", 'Interactive')
                RunLevel                   = @("$($task.Principal.RunLevel)", 'Limited')
                ExecutionTimeLimit         = @("$($task.Settings.ExecutionTimeLimit)", 'PT0S')
                MultipleInstances          = @("$($task.Settings.MultipleInstances)", 'IgnoreNew')
                DisallowStartIfOnBatteries = @("$($task.Settings.DisallowStartIfOnBatteries)", 'False')
                StopIfGoingOnBatteries     = @("$($task.Settings.StopIfGoingOnBatteries)", 'False')
                StartWhenAvailable         = @("$($task.Settings.StartWhenAvailable)", 'True')
            }
            $case.details.settings = [ordered]@{}
            $wrong = @()
            foreach ($key in $checks.Keys) {
                $case.details.settings[$key] = $checks[$key][0]
                if ($checks[$key][0] -ne $checks[$key][1]) { $wrong += "$key is $($checks[$key][0])" }
            }
            if ($wrong) { throw "registered with other settings: $($wrong -join '; ')" }
        }

        Invoke-Case 'start' {
            param($case)
            $clock = [Diagnostics.Stopwatch]::StartNew()
            Start-ScheduledTask -TaskName $proofTask
            $supervisor = Wait-ForEvent $supervisorLog 'supervisor_start' 1 120
            $case.details.seconds_to_supervisor_log = [Math]::Round($clock.Elapsed.TotalSeconds, 3)
            $start = Wait-ForEvent $supervisorLog 'start' 1 120
            $fake = Wait-ForEvent $fakeLog 'start' 1 120
            $heartbeat = Join-Path $proofDir 'heartbeat'
            if (-not (Wait-Until { Test-Path -LiteralPath $heartbeat } 30)) { throw 'the fake never beat' }
            $case.details.seconds_to_first_beat = [Math]::Round($clock.Elapsed.TotalSeconds, 3)
            $case.details.supervisor_pid = $supervisor.supervisor_pid
            $case.details.child_pid = $start.pid
            $case.details.fake_pid = $fake.pid
            $case.details.fake_parent_pid = $fake.ppid
            $case.details.fake_nextron_env = $fake.nextron_env
            $case.details.state = "$((Get-ScheduledTask -TaskName $proofTask).State)"
            if ($fake.nextron_env -ne $Env) { throw "the fake saw NEXTRON_ENV=$($fake.nextron_env)" }
        }

        Invoke-Case 'ignore_new' {
            param($case)
            try {
                Start-ScheduledTask -TaskName $proofTask
                $case.details.second_start = 'accepted'
            } catch {
                $case.details.second_start = "refused: $($_.Exception.Message)"
            }
            Start-Sleep -Seconds 5
            $starts = @(Get-Events $supervisorLog 'supervisor_start').Count
            $supervisors = @(Get-SpikeProcesses $proofDir | Where-Object { $_.Name -eq 'powershell.exe' })
            $case.details.supervisor_starts = $starts
            $case.details.supervisor_processes = $supervisors.Count
            $case.details.state = "$((Get-ScheduledTask -TaskName $proofTask).State)"
            if ($starts -ne 1 -or $supervisors.Count -ne 1) { throw 'a second supervisor started' }
        }

        Invoke-Case 'crash' {
            param($case)
            $rounds = @()
            foreach ($round in 1, 2) {
                $exits = @(Get-Events $supervisorLog 'exit').Count
                $backoffs = @(Get-Events $supervisorLog 'backoff').Count
                $starts = @(Get-Events $supervisorLog 'start').Count
                $fakeStarts = @(Get-Events $fakeLog 'start').Count
                $fake = Wait-ForEvent $fakeLog 'start' $fakeStarts 5
                Stop-Process -Id $fake.pid -Force
                $exit = Wait-ForEvent $supervisorLog 'exit' ($exits + 1) 30
                $backoff = Wait-ForEvent $supervisorLog 'backoff' ($backoffs + 1) 30
                $restart = Wait-ForEvent $supervisorLog 'start' ($starts + 1) 60
                [void](Wait-ForEvent $fakeLog 'start' ($fakeStarts + 1) 60)
                $rounds += [ordered]@{
                    killed_fake_pid    = $fake.pid
                    exit_code          = $exit.code
                    uptime_s           = $exit.uptime_s
                    backoff_s          = $backoff.wait_s
                    exit_to_restart_s  = Get-Seconds $exit.ts $restart.ts
                    new_child_pid      = $restart.pid
                }
            }
            $case.details.rounds = $rounds
            $waits = @($rounds | ForEach-Object { [double]$_.backoff_s })
            if ($waits[0] -ne 2 -or $waits[1] -ne 4) { throw "backoffs were $($waits -join ', '), not 2, 4" }
        }

        Invoke-Case 'stall' {
            param($case)
            $start = Get-Events $supervisorLog 'start' | Select-Object -Last 1
            $fake = Get-Events $fakeLog 'start' | Select-Object -Last 1
            $tree = @([int]$start.pid, [int]$fake.ppid, [int]$fake.pid) | Select-Object -Unique
            $stales = @(Get-Events $supervisorLog 'stale').Count
            $exits = @(Get-Events $supervisorLog 'exit').Count
            $starts = @(Get-Events $supervisorLog 'start').Count
            $fakeStarts = @(Get-Events $fakeLog 'start').Count
            Send-SpikeCommand $proofDir 'stall'
            $stale = Wait-ForEvent $supervisorLog 'stale' ($stales + 1) 60
            $exit = Wait-ForEvent $supervisorLog 'exit' ($exits + 1) 30
            $kills = @(Get-Events $supervisorLog 'kill' | Where-Object { $_.pid -eq $stale.pid })
            [void](Wait-Until { -not ($tree | Where-Object { Test-Running $_ }) } 10)
            $left = @($tree | Where-Object { Test-Running $_ })
            $restart = Wait-ForEvent $supervisorLog 'start' ($starts + 1) 60
            [void](Wait-ForEvent $fakeLog 'start' ($fakeStarts + 1) 60)
            $case.details.tree_pids = $tree
            $case.details.quiet_s = $stale.quiet_s
            $case.details.before_first_beat = $stale.before_first_beat
            $case.details.kills = @($kills | ForEach-Object { "$($_.method) ok=$($_.ok)" })
            $case.details.exit_killed = $exit.killed
            $case.details.left_running = $left
            $case.details.stale_to_restart_s = Get-Seconds $stale.ts $restart.ts
            if ($stale.before_first_beat) { throw 'the stale kill came before the first beat' }
            if ($left) { throw "the killed tree left $($left -join ', ') running" }
        }

        Invoke-Case 'exit3' {
            param($case)
            Send-SpikeCommand $proofDir 'exit 3'
            $stop = Wait-ForEvent $supervisorLog 'stop' 1 60
            $ended = Wait-Until { "$((Get-ScheduledTask -TaskName $proofTask).State)" -ne 'Running' } 30
            $info = Get-ScheduledTaskInfo -TaskName $proofTask
            $left = @(Get-SpikeProcesses $proofDir | ForEach-Object { [int]$_.ProcessId })
            $case.details.stop_logged_at = $stop.ts
            $case.details.task_ended = $ended
            $case.details.last_task_result = $info.LastTaskResult
            $case.details.left_running = $left
            if (-not $ended) { throw 'the task still shows Running' }
            if ($info.LastTaskResult -ne 3) { throw "LastTaskResult is $($info.LastTaskResult), not 3" }
            if ($left) { throw "processes left running: $($left -join ', ')" }
        }

        Invoke-Case 'stop_task' {
            param($case)
            $supervisors = @(Get-Events $supervisorLog 'supervisor_start').Count
            $starts = @(Get-Events $supervisorLog 'start').Count
            $fakeStarts = @(Get-Events $fakeLog 'start').Count
            $clock = [Diagnostics.Stopwatch]::StartNew()
            Start-ScheduledTask -TaskName $proofTask
            $supervisor = Wait-ForEvent $supervisorLog 'supervisor_start' ($supervisors + 1) 120
            $case.details.seconds_to_supervisor_log = [Math]::Round($clock.Elapsed.TotalSeconds, 3)
            $start = Wait-ForEvent $supervisorLog 'start' ($starts + 1) 120
            $fake = Wait-ForEvent $fakeLog 'start' ($fakeStarts + 1) 120
            $tree = [ordered]@{
                supervisor = [int]$supervisor.supervisor_pid
                child      = [int]$start.pid
                launcher   = [int]$fake.ppid
                fake       = [int]$fake.pid
            }
            Stop-ScheduledTask -TaskName $proofTask
            [void](Wait-Until { -not ($tree.Values | Where-Object { Test-Running $_ }) } 15)
            $left = @($tree.Keys | Where-Object { Test-Running $tree[$_] })
            $case.details.tree_pids = $tree
            $case.details.left_running = $left
            $case.details.state = "$((Get-ScheduledTask -TaskName $proofTask).State)"
            if ($left) {
                $case.status = 'observed'
                $case.details.note = 'Stop-ScheduledTask left these running; the proof stops them now'
                foreach ($key in $left) { Stop-Process -Id $tree[$key] -Force -ErrorAction SilentlyContinue }
            }
        }
    } finally {
        $cleanup = [ordered]@{ name = 'cleanup'; status = 'pass'; details = [ordered]@{} }
        [void]$script:Cases.Add($cleanup)
        try {
            $cleanup.details.stop_errors = @()
            $ours = Get-ScheduledTask -TaskName $proofTask -ErrorAction SilentlyContinue
            if ($ours) {
                if ("$($ours.State)" -eq 'Running') {
                    try {
                        Stop-ScheduledTask -TaskName $proofTask
                    } catch {
                        $cleanup.details.stop_errors += "${proofTask}: $($_.Exception.Message)"
                    }
                }
                Unregister-ScheduledTask -TaskName $proofTask -Confirm:$false
                $cleanup.details.unregistered = $proofTask
            }
            $others = @(Get-ScheduledTask -TaskName "$ProofPrefix*" -ErrorAction SilentlyContinue |
                    Where-Object { $_.TaskName -match '^NextronSpikeTest-[0-9a-f]{8}$' })
            foreach ($other in $others) {
                if ("$($other.State)" -eq 'Running') {
                    try {
                        Stop-ScheduledTask -InputObject $other
                    } catch {
                        $cleanup.details.stop_errors += "$($other.TaskName): $($_.Exception.Message)"
                    }
                }
                Unregister-ScheduledTask -InputObject $other -Confirm:$false
            }
            $cleanup.details.earlier_leftovers_unregistered = @($others | ForEach-Object { $_.TaskName })
        } catch {
            $cleanup.status = if (Test-Denied $_.Exception) { 'denied' } else { 'fail' }
            $cleanup.details.error = $_.Exception.Message
        }
        $cleanup.details.leftover_processes_stopped = Stop-SpikeProcesses $proofDir
        [void](Wait-Until { @(Get-SpikeProcesses $proofDir).Count -eq 0 } 15)
        $tasksLeft = @(Get-ScheduledTask -TaskName 'NextronSpikeTest*' -ErrorAction SilentlyContinue |
                ForEach-Object { $_.TaskName })
        $processesLeft = @(Get-SpikeProcesses $proofDir | ForEach-Object { [int]$_.ProcessId })
        $cleanup.details.tasks_left = $tasksLeft
        $cleanup.details.processes_left = $processesLeft
        if ($tasksLeft -or $processesLeft) { $cleanup.status = 'fail' }
        Write-Output ('{0,-12} {1}' -f 'cleanup', $cleanup.status)
        $results.ended_at = [DateTime]::UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", $Invariant)
        $json = ConvertTo-Json -InputObject $results -Depth 8
        [IO.File]::WriteAllText((Join-Path $proofDir 'results.json'), $json, $Utf8NoBom)
        Write-Output "Results: $(Join-Path $proofDir 'results.json')"
        Write-Output $json
    }
    $bad = @($script:Cases | Where-Object { $_.status -in @('fail', 'denied', 'skipped') })
    if ($bad) { $script:ExitCode = 1 }
}

# --- Main ----------------------------------------------------------------------------------------

$refusal = Get-Refusal
if ($refusal) {
    [Console]::Error.WriteLine("oq_a9: refused: $refusal")
    exit 2
}
$script:ExitCode = 0
switch ($Action) {
    'DryRun' { Invoke-DryRun }
    'Register' { Invoke-Register }
    'Status' { Invoke-Status }
    'Kill' { Invoke-Kill }
    'Stall' { Invoke-Control 'stall' 'The supervisor kills it after 5 minutes without a beat; run -Action Status after 6 minutes.' }
    'Exit3' { Invoke-Control 'exit 3' 'The supervisor stops looping and exits 3; run -Action Status in a minute.' }
    'Unregister' { Invoke-Unregister }
    'Prove' { Invoke-Prove }
}
exit $script:ExitCode
