<#
.SYNOPSIS
Prints, registers or removes the logon task "Nextron Engine (prod)" that runs
scripts\run.ps1 -Env prod (Story 1.21, AD-20).

.DESCRIPTION
Run it from a normal (not "Run as administrator") Windows PowerShell 5.1, in nextron-engine:

  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_logon_task.ps1 [-Register | -Uninstall]

  (no switch)  the dry run: print the task it would register; register nothing.
  -Register    register the task for the current user, replacing one of that name, then print
               Get-ScheduledTask and Get-ScheduledTaskInfo (a failed read-back is one stderr
               line; the exit code stays 0). It starts at the next logon.
  -Uninstall   stop the task when it runs (which ends run.ps1 and the engine with it), then
               remove it. With no such task it says there is nothing to remove. A failed stop
               removes nothing and exits 1.

Every Task Scheduler call names the root folder (-TaskPath \), so a task of the same name in
another folder is never read or changed. The dry run and -Register also print the uv.exe found
on PATH, or say that none was found (run.ps1 then cannot start the engine).

The task, built once and shared by every mode, is the one the OQ-A9 spike proved
(docs\spikes\oq-a9-logon-task.md): an AtLogOn trigger for the current user; the principal
Interactive/Limited; the settings ExecutionTimeLimit PT0S, AllowStartIfOnBatteries,
DontStopIfGoingOnBatteries, StartWhenAvailable and MultipleInstances IgnoreNew; and the action

  <SystemRoot>\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive
    -ExecutionPolicy Bypass -WindowStyle Hidden -File "<nextron-engine>\scripts\run.ps1" -Env prod

with nextron-engine as its working folder.

The refusals come first, each one line on stderr and exit 2 with nothing called: an elevated
PowerShell, -Register with -Uninstall, and, under NEXTRON_ENV=test, a Task Scheduler command
that comes from a module (the tests run this script only with every such command shadowed).
#>
[CmdletBinding()]
param(
    [switch]$Register,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$TaskName = 'Nextron Engine (prod)'
# The root folder only: a task of the same name in another folder is never read or changed.
$TaskPath = '\'
$Description = 'Nextron Outreach Engine (prod): at logon, scripts\run.ps1 -Env prod starts and supervises nextron serve. Remove it with scripts\install_logon_task.ps1 -Uninstall.'
$EngineRoot = [IO.Path]::GetFullPath([IO.Path]::Combine($PSScriptRoot, '..')).TrimEnd('\')
$RunScript = [IO.Path]::Combine($EngineRoot, 'scripts', 'run.ps1')
$PowerShellPath = [IO.Path]::Combine($env:SystemRoot, 'System32\WindowsPowerShell\v1.0\powershell.exe')
$Me = [Security.Principal.WindowsIdentity]::GetCurrent().Name

# The commands that read or change Task Scheduler's state. The dry run calls none of them; the
# New-ScheduledTask* builders only make objects in memory.
$StateCommands = @(
    'Get-ScheduledTask', 'Get-ScheduledTaskInfo', 'Register-ScheduledTask', 'Stop-ScheduledTask',
    'Unregister-ScheduledTask'
)

# --- Refusals ------------------------------------------------------------------------------------

function Test-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ModuleCommands {
    # Each state command that would run the real ScheduledTasks module (or any module's) code.
    $found = @()
    foreach ($name in $StateCommands) {
        $command = Get-Command -Name $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command -and $command.Module) { $found += "$name ($($command.Module.Name))" }
    }
    return , $found
}

function Get-Refusal {
    param([bool]$Elevated, [bool]$Register, [bool]$Uninstall, [string]$NextronEnv)
    if ($Elevated) {
        return 'this PowerShell is elevated; run it from a normal one, so the task runs as you, not as an administrator'
    }
    if ($Register -and $Uninstall) {
        return 'use -Register or -Uninstall, not both'
    }
    if ("$NextronEnv".Trim() -eq 'test') {
        $real = Get-ModuleCommands
        if ($real.Count -gt 0) {
            return "NEXTRON_ENV=test, so the real Task Scheduler commands are never called: $($real -join ', ')"
        }
    }
    return $null
}

# --- The task, built once and shared by every mode -----------------------------------------------

function New-TaskParts {
    # Windows paths can't hold a double quote, and $RunScript has no trailing backslash.
    $arguments = @(
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
        '-File', ('"' + $RunScript + '"'), '-Env', 'prod'
    ) -join ' '
    return [ordered]@{
        Trigger   = New-ScheduledTaskTrigger -AtLogOn -User $Me
        Principal = New-ScheduledTaskPrincipal -UserId $Me -LogonType Interactive -RunLevel Limited
        Settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -MultipleInstances IgnoreNew
        Action    = New-ScheduledTaskAction -Execute $PowerShellPath -Argument $arguments `
            -WorkingDirectory $EngineRoot
    }
}

function Format-TaskParts {
    param([System.Collections.IDictionary]$Parts)
    $trigger = $Parts.Trigger
    $principal = $Parts.Principal
    $settings = $Parts.Settings
    $action = $Parts.Action
    return @(
        "Task name   : $TaskName (folder $TaskPath)"
        "Trigger     : AtLogOn ($($trigger.CimClass.CimClassName)), user $($trigger.UserId)"
        "Principal   : UserId $($principal.UserId), LogonType $($principal.LogonType), RunLevel $($principal.RunLevel)"
        ("Settings    : ExecutionTimeLimit $($settings.ExecutionTimeLimit), " +
            "DisallowStartIfOnBatteries $($settings.DisallowStartIfOnBatteries), " +
            "StopIfGoingOnBatteries $($settings.StopIfGoingOnBatteries), " +
            "StartWhenAvailable $($settings.StartWhenAvailable), " +
            "MultipleInstances $($settings.MultipleInstances), Priority $($settings.Priority)")
        "Action      : $($action.Execute) $($action.Arguments)"
        "Working dir : $($action.WorkingDirectory)"
        "Description : $Description"
        (Format-UvLine)
    )
}

function Format-UvLine {
    # run.ps1 starts the engine through uv.exe from PATH; without it, it retries a bare uv.exe
    # forever, so say so here (it isn't a refusal).
    $uv = Get-Command -Name 'uv.exe' -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($uv) { return "uv.exe      : $($uv.Path)" }
    return 'uv.exe      : not found on PATH; run.ps1 cannot start the engine'
}

function Format-TaskEvidence {
    # Get-ScheduledTask and Get-ScheduledTaskInfo for the task, as text.
    $task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath $TaskPath
    $text = @(
        '--- Get-ScheduledTask'
        ($task | Format-List TaskName, TaskPath, State, Description | Out-String -Width 300).Trim()
        ($task.Principal | Format-List UserId, LogonType, RunLevel | Out-String -Width 300).Trim()
        ($task.Settings | Format-List ExecutionTimeLimit, DisallowStartIfOnBatteries,
            StopIfGoingOnBatteries, StartWhenAvailable, MultipleInstances, Priority |
            Out-String -Width 300).Trim()
        ($task.Actions | Format-List Execute, Arguments, WorkingDirectory | Out-String -Width 300).Trim()
        '--- Get-ScheduledTaskInfo'
        ($info | Format-List LastRunTime, LastTaskResult, NextRunTime | Out-String -Width 300).Trim()
    )
    return ($text | Where-Object { $_ }) -join [Environment]::NewLine
}

# --- Modes ---------------------------------------------------------------------------------------

function Invoke-DryRun {
    $parts = New-TaskParts
    Write-Output 'Nextron logon task: dry run. Nothing is registered; -Register registers it.'
    Format-TaskParts $parts | Write-Output
}

function Invoke-Register {
    $parts = New-TaskParts
    $task = New-ScheduledTask -Trigger $parts.Trigger -Principal $parts.Principal `
        -Settings $parts.Settings -Action $parts.Action -Description $Description
    Format-TaskParts $parts | Write-Output
    try {
        [void](Register-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -InputObject $task -Force)
    } catch {
        [Console]::Error.WriteLine("install_logon_task: registration failed: $($_.Exception.Message) (see docs\spikes\oq-a9-logon-task.md, Fallbacks)")
        $script:ExitCode = 1
        return
    }
    Write-Output "Registered '$TaskName' without elevation; it starts at your next logon."
    try {
        $evidence = Format-TaskEvidence
    } catch {
        # Registered all the same: the exit code stays 0.
        [Console]::Error.WriteLine("install_logon_task: registered; reading it back failed: $($_.Exception.Message)")
        return
    }
    Write-Output $evidence
}

function Invoke-Uninstall {
    $task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Output "Nothing to remove: no task named '$TaskName' is registered."
        return
    }
    if ("$($task.State)" -eq 'Running') {
        try {
            Stop-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
        } catch {
            # run.ps1 and the engine may still run: removing the task now would orphan them.
            [Console]::Error.WriteLine("install_logon_task: stopping '$TaskName' failed, so nothing was removed: $($_.Exception.Message)")
            $script:ExitCode = 1
            return
        }
        Write-Output "Stopped '$TaskName': run.ps1 and the engine ended with it."
    }
    try {
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false
    } catch {
        [Console]::Error.WriteLine("install_logon_task: removing '$TaskName' failed: $($_.Exception.Message)")
        $script:ExitCode = 1
        return
    }
    if (Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue) {
        [Console]::Error.WriteLine("install_logon_task: '$TaskName' is still registered")
        $script:ExitCode = 1
        return
    }
    Write-Output "Removed '$TaskName'. Checked: no task of that name remains."
}

# --- Main ----------------------------------------------------------------------------------------

$refusal = Get-Refusal -Elevated (Test-Elevated) -Register $Register.IsPresent `
    -Uninstall $Uninstall.IsPresent -NextronEnv $env:NEXTRON_ENV
if ($refusal) {
    [Console]::Error.WriteLine("install_logon_task: refused: $refusal")
    exit 2
}
$script:ExitCode = 0
if ($Register) {
    Invoke-Register
} elseif ($Uninstall) {
    Invoke-Uninstall
} else {
    Invoke-DryRun
}
exit $script:ExitCode
