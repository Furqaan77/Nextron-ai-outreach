<#
.SYNOPSIS
The engine's supervisor (Story 1.21, AD-20): starts `nextron serve` for one environment and
restarts it after a crash or a hang. The logon task (scripts\install_logon_task.ps1) runs it
with -Env prod.

.DESCRIPTION
Run it from a normal Windows PowerShell 5.1:

  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run.ps1 -Env <env> [-Stop | -Restart | -Status]

With no switch it supervises: it starts `uv run --frozen python -m nextron serve` in the
nextron-engine folder, with NEXTRON_ENV=<env> and NEXTRON_DATA_DIR=<data dir> set for the child
only, and loops:

- Each child starts suspended, in a new process group, inside a kill-on-close job object of its
  own, so a kill takes uv, the venv launcher and Python together, and ending this supervisor
  (ending its task in Task Scheduler included) ends the child's whole tree with it.
- Any exit restarts the child after a backoff that starts at -BackoffStartSeconds and doubles up
  to -BackoffMaxSeconds. A child up -ResetAfterSeconds since its first heartbeat resets it.
- The child's own exit 3 (the engine's lock is held) ends the loop: the supervisor exits 3.
  Five exits with code 2 in a row (a failing startup check or migration) end it too: exit 2.
- Stale is counted in this loop's own polls (a relative wait of -PollSeconds, so time asleep never
  counts): each poll that sees <data dir>\heartbeat unchanged adds -PollSeconds, and a change
  resets the count. At -StaleAfterSeconds after the child's first beat, or -StartGraceSeconds
  before it, the job is killed and the child restarted.
- `-Stop` and `-Restart` (run from a second PowerShell) leave a one-shot request in
  <data dir>\supervisor-request. At its next poll the supervisor sends CTRL_BREAK to the child's
  process group, waits up to -StopGraceSeconds for the job to empty (then kills it), and stops
  (exit 0) or starts the child again at once.
- `-Status` prints whether a supervisor runs, the heartbeat's age and the log's last lines.

The data dir is the engine's: NEXTRON_DATA_DIR (with %VAR% and a leading ~ expanded, and a
relative path taken against nextron-engine), else %LOCALAPPDATA%\Nextron\<env>. One supervisor
runs per data dir: it holds <data dir>\supervisor.lock open for its whole run, and a second one
exits 3.

Events go to <data dir>\logs\supervisor.log as JSON lines (UTF-8, no BOM, UTC times), rotated at
1 MiB with 3 old files kept: supervisor_start, request_discarded, start, start_failed, exit,
stale, kill, request, backoff, stop, max_restarts, supervisor_refused and supervisor_error.

-ServeCommand, -MaxRestarts, -FailJobKillOnce and the timing parameters are for tests only.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('dev', 'test', 'prod')]
    [string]$Env,
    [switch]$Stop,
    [switch]$Restart,
    [switch]$Status,
    # Tests only: the command to supervise instead of uv run --frozen python -m nextron serve.
    [string[]]$ServeCommand,
    # Tests only: end with exit 0 once this many restarts are used up; -1 restarts forever.
    [ValidateRange(-1, 1000000)][int]$MaxRestarts = -1,
    # Tests only: the first kill skips TerminateJobObject, so the process_kill fallback runs.
    [switch]$FailJobKillOnce,
    [ValidateRange(0.01, 86400)][double]$BackoffStartSeconds = 5,
    [ValidateRange(0.01, 86400)][double]$BackoffMaxSeconds = 300,
    [ValidateRange(0.01, 86400)][double]$ResetAfterSeconds = 600,
    [ValidateRange(0.01, 86400)][double]$StaleAfterSeconds = 300,
    [ValidateRange(0.01, 86400)][double]$StartGraceSeconds = 900,
    [ValidateRange(0.01, 86400)][double]$PollSeconds = 5,
    [ValidateRange(0.01, 86400)][double]$StopGraceSeconds = 60,
    [ValidateRange(0.01, 86400)][double]$RequestTimeoutSeconds = 120
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Invariant = [Globalization.CultureInfo]::InvariantCulture
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false
$EngineRoot = [IO.Path]::GetFullPath([IO.Path]::Combine($PSScriptRoot, '..')).TrimEnd('\')
$LogMaxBytes = 1MB
$LogKeep = 3
$Exit2Limit = 5
$KillWaitMilliseconds = 10000
$OrphanWaitMilliseconds = 2000
$SliceMilliseconds = 50

# --- Native calls -------------------------------------------------------------------------------

$NativeSource = @'
using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

namespace NextronRun {
    public sealed class Child {
        public IntPtr Process;
        public IntPtr Thread;
        public int Id;
    }

    public static class Native {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct STARTUPINFO {
            public int cb;
            public string lpReserved, lpDesktop, lpTitle;
            public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
            public short wShowWindow, cbReserved2;
            public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct PROCESS_INFORMATION {
            public IntPtr hProcess, hThread;
            public int dwProcessId, dwThreadId;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct BASIC_LIMIT {
            public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass, SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct IO_COUNTERS {
            public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct EXTENDED_LIMIT {
            public BASIC_LIMIT Basic;
            public IO_COUNTERS Io;
            public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct ACCOUNTING {
            public long TotalUserTime, TotalKernelTime, ThisPeriodTotalUserTime, ThisPeriodTotalKernelTime;
            public uint TotalPageFaultCount, TotalProcesses, ActiveProcesses, TotalTerminatedProcesses;
        }

        const uint CREATE_SUSPENDED = 0x4;
        const uint CREATE_NEW_PROCESS_GROUP = 0x200;
        const uint CREATE_UNICODE_ENVIRONMENT = 0x400;
        const uint KILL_ON_JOB_CLOSE = 0x2000;
        const int BASIC_ACCOUNTING_INFORMATION = 1;
        const int BASIC_PROCESS_ID_LIST = 3;
        const int EXTENDED_LIMIT_INFORMATION = 9;
        const uint CTRL_BREAK_EVENT = 1;
        const uint WAIT_OBJECT_0 = 0;
        const uint WAIT_TIMEOUT = 0x102;

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref EXTENDED_LIMIT info, int length);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool QueryInformationJobObject(IntPtr job, int infoClass, out ACCOUNTING info, int length, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool QueryInformationJobObject(IntPtr job, int infoClass, IntPtr info, int length, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool TerminateJobObject(IntPtr job, uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern bool CreateProcessW(string application, StringBuilder commandLine, IntPtr processAttributes,
            IntPtr threadAttributes, bool inheritHandles, uint flags, IntPtr environment, string directory,
            ref STARTUPINFO startup, out PROCESS_INFORMATION info);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool TerminateProcess(IntPtr process, uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GenerateConsoleCtrlEvent(uint ctrlEvent, uint processGroup);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool CloseHandle(IntPtr handle);

        // A new job whose processes all end when its last handle closes.
        public static IntPtr NewJob() {
            IntPtr job = CreateJobObjectW(IntPtr.Zero, null);
            if (job == IntPtr.Zero) throw new Win32Exception();
            EXTENDED_LIMIT info = new EXTENDED_LIMIT();
            info.Basic.LimitFlags = KILL_ON_JOB_CLOSE;
            if (!SetInformationJobObject(job, EXTENDED_LIMIT_INFORMATION, ref info, Marshal.SizeOf(typeof(EXTENDED_LIMIT)))) {
                Win32Exception error = new Win32Exception();
                CloseHandle(job);
                throw error;
            }
            return job;
        }

        public static uint ActiveProcesses(IntPtr job) {
            ACCOUNTING info;
            if (!QueryInformationJobObject(job, BASIC_ACCOUNTING_INFORMATION, out info, Marshal.SizeOf(typeof(ACCOUNTING)), IntPtr.Zero))
                throw new Win32Exception();
            return info.ActiveProcesses;
        }

        public static int[] ProcessIds(IntPtr job) {
            const int capacity = 1024;
            int length = 8 + IntPtr.Size * capacity;
            IntPtr buffer = Marshal.AllocHGlobal(length);
            try {
                if (!QueryInformationJobObject(job, BASIC_PROCESS_ID_LIST, buffer, length, IntPtr.Zero))
                    throw new Win32Exception();
                int count = Marshal.ReadInt32(buffer, 4);
                int[] ids = new int[count];
                for (int i = 0; i < count; i++) ids[i] = (int)Marshal.ReadIntPtr(buffer, 8 + i * IntPtr.Size).ToInt64();
                return ids;
            } finally {
                Marshal.FreeHGlobal(buffer);
            }
        }

        // Starts commandLine suspended, in a new process group, with this process's environment plus
        // the overrides, and puts it in the job before it runs a single instruction.
        public static Child Start(IntPtr job, string commandLine, string directory, string[] names, string[] values) {
            SortedDictionary<string, string> environment = new SortedDictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (DictionaryEntry entry in Environment.GetEnvironmentVariables()) {
                string name = (string)entry.Key;
                if (name.Length == 0 || name[0] == '=') continue;
                environment[name] = (string)entry.Value;
            }
            for (int i = 0; i < names.Length; i++) {
                environment.Remove(names[i]);
                environment.Add(names[i], values[i]);
            }
            StringBuilder block = new StringBuilder();
            foreach (KeyValuePair<string, string> pair in environment) {
                block.Append(pair.Key).Append('=').Append(pair.Value).Append('\0');
            }
            block.Append('\0');
            STARTUPINFO startup = new STARTUPINFO();
            startup.cb = Marshal.SizeOf(typeof(STARTUPINFO));
            PROCESS_INFORMATION info;
            IntPtr environmentBlock = Marshal.StringToHGlobalUni(block.ToString());
            try {
                uint flags = CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP | CREATE_UNICODE_ENVIRONMENT;
                if (!CreateProcessW(null, new StringBuilder(commandLine), IntPtr.Zero, IntPtr.Zero, true, flags,
                        environmentBlock, directory, ref startup, out info))
                    throw new Win32Exception();
            } finally {
                Marshal.FreeHGlobal(environmentBlock);
            }
            if (!AssignProcessToJobObject(job, info.hProcess) || ResumeThread(info.hThread) == 0xFFFFFFFF) {
                Win32Exception error = new Win32Exception();
                TerminateProcess(info.hProcess, 1);
                CloseHandle(info.hThread);
                CloseHandle(info.hProcess);
                throw error;
            }
            Child child = new Child();
            child.Process = info.hProcess;
            child.Thread = info.hThread;
            child.Id = info.dwProcessId;
            return child;
        }

        // True once the handle is signalled (the process ended); a relative wait, so time asleep never counts.
        public static bool Wait(IntPtr handle, int milliseconds) {
            uint result = WaitForSingleObject(handle, unchecked((uint)milliseconds));
            if (result == WAIT_OBJECT_0) return true;
            if (result == WAIT_TIMEOUT) return false;
            throw new Win32Exception();
        }

        public static int ExitCode(IntPtr process) {
            uint code;
            if (!GetExitCodeProcess(process, out code)) throw new Win32Exception();
            return unchecked((int)code);
        }

        public static bool CtrlBreak(int processGroup) {
            return GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, (uint)processGroup);
        }

        public static void Terminate(IntPtr job, uint exitCode) {
            if (!TerminateJobObject(job, exitCode)) throw new Win32Exception();
        }

        public static void Close(IntPtr handle) {
            if (handle != IntPtr.Zero) CloseHandle(handle);
        }
    }
}
'@

function Add-NativeType {
    if (-not ('NextronRun.Native' -as [type])) {
        Add-Type -TypeDefinition $NativeSource -Language CSharp
    }
}

# --- Paths --------------------------------------------------------------------------------------

function Get-UserProfile {
    $profileDir = [Environment]::GetEnvironmentVariable('USERPROFILE')
    if ([string]::IsNullOrEmpty($profileDir)) {
        $profileDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    }
    return $profileDir
}

function Resolve-DataDir {
    # The engine's own rule (nextron\bootstrap.py): NEXTRON_DATA_DIR, an empty value counting as
    # unset, with %VAR% and a leading ~ expanded and a relative path taken against nextron-engine;
    # otherwise %LOCALAPPDATA%\Nextron\<env>.
    $value = [Environment]::GetEnvironmentVariable('NEXTRON_DATA_DIR')
    if ([string]::IsNullOrEmpty($value)) {
        $local = [Environment]::GetEnvironmentVariable('LOCALAPPDATA')
        if ([string]::IsNullOrEmpty($local)) {
            $local = [IO.Path]::Combine((Get-UserProfile), 'AppData\Local')
        }
        $path = [IO.Path]::Combine($local, 'Nextron', $Env)
    } else {
        $path = [Environment]::ExpandEnvironmentVariables($value)
        if ($path -eq '~' -or $path -match '^~[\\/]') {
            $path = (Get-UserProfile) + $path.Substring(1)
        }
        if ($path -match '^[\\/](?![\\/])') {
            # Rooted without a drive: on the engine's drive, as Python joins it.
            $path = [IO.Path]::GetPathRoot($EngineRoot).TrimEnd('\') + $path
        } elseif (-not [IO.Path]::IsPathRooted($path)) {
            $path = [IO.Path]::Combine($EngineRoot, $path)
        }
    }
    $full = [IO.Path]::GetFullPath($path)
    if ($full.Length -gt [IO.Path]::GetPathRoot($full).Length) { $full = $full.TrimEnd('\') }
    return $full
}

$DataDir = Resolve-DataDir
$LogDir = [IO.Path]::Combine($DataDir, 'logs')
$LogPath = [IO.Path]::Combine($LogDir, 'supervisor.log')
$HeartbeatPath = [IO.Path]::Combine($DataDir, 'heartbeat')
$LockPath = [IO.Path]::Combine($DataDir, 'supervisor.lock')
$RequestPath = [IO.Path]::Combine($DataDir, 'supervisor-request')
$script:OwnsLog = $false
$script:FailJobKillPending = $FailJobKillOnce.IsPresent

# --- The log ------------------------------------------------------------------------------------

function Step-Log {
    # Rotates supervisor.log once it reaches $LogMaxBytes: .1 is the newest old file, .3 the
    # oldest. Only the supervisor holding the lock rotates. A failure is retried at the next write.
    try {
        $info = New-Object IO.FileInfo $LogPath
        if (-not $info.Exists -or $info.Length -lt $LogMaxBytes) { return }
        for ($n = $LogKeep; $n -ge 1; $n--) {
            $older = "$LogPath.$n"
            $newer = if ($n -eq 1) { $LogPath } else { "$LogPath.$($n - 1)" }
            if ([IO.File]::Exists($newer)) {
                if ([IO.File]::Exists($older)) { [IO.File]::Delete($older) }
                [IO.File]::Move($newer, $older)
            }
        }
    } catch {
        return
    }
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
    if ($script:OwnsLog) { Step-Log }
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            [IO.File]::AppendAllText($LogPath, $line, $Utf8NoBom)
            return
        } catch {
            Start-Sleep -Milliseconds 50
        }
    }
}

function Get-ErrorText {
    # The innermost message: a .NET call's error comes wrapped in "Exception calling ...".
    param($ErrorRecord)
    $exception = $ErrorRecord.Exception
    while ($exception.InnerException) { $exception = $exception.InnerException }
    return $exception.Message
}

function Read-SharedText {
    # A file another process may be writing, or $null when it is missing.
    param([string]$Path)
    try {
        $share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
        $stream = New-Object IO.FileStream($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, $share)
    } catch {
        return $null
    }
    try {
        $reader = New-Object IO.StreamReader($stream, $Utf8NoBom)
        return $reader.ReadToEnd()
    } finally {
        $stream.Dispose()
    }
}

function Get-LogLines {
    # The complete lines of supervisor.log.1 and supervisor.log, oldest first.
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($path in @("$LogPath.1", $LogPath)) {
        $text = Read-SharedText $path
        if (-not $text) { continue }
        $parts = $text.Split("`n")
        for ($i = 0; $i -lt $parts.Length - 1; $i++) {
            if ($parts[$i].Trim()) { $lines.Add($parts[$i].Trim()) }
        }
    }
    return , $lines.ToArray()
}

# --- The lock and requests ----------------------------------------------------------------------

function Test-SharingViolation {
    param($ErrorRecord)
    $exception = $ErrorRecord.Exception
    while ($exception.InnerException) { $exception = $exception.InnerException }
    if ($exception -is [IO.FileNotFoundException] -or $exception -is [IO.DirectoryNotFoundException]) {
        return $false
    }
    # ERROR_SHARING_VIOLATION (32) or ERROR_LOCK_VIOLATION (33).
    return ($exception -is [IO.IOException]) -and (@(32, 33) -contains ($exception.HResult -band 0xFFFF))
}

function Test-SupervisorRunning {
    # Whether a supervisor holds <data dir>\supervisor.lock. Creates nothing.
    try {
        $share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
        $stream = [IO.File]::Open($LockPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, $share)
        $stream.Dispose()
        return $false
    } catch {
        if (Test-SharingViolation $_) { return $true }
        $exception = $_.Exception
        while ($exception.InnerException) { $exception = $exception.InnerException }
        if ($exception -is [IO.FileNotFoundException] -or $exception -is [IO.DirectoryNotFoundException]) {
            return $false
        }
        throw
    }
}

function Open-SupervisorLock {
    # The lock held, unshared, for this supervisor's whole run; $null when another one holds it.
    # A few tries: a -Status or -Stop probe may hold the file open for a moment.
    for ($try = 1; $try -le 10; $try++) {
        try {
            return [IO.File]::Open($LockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite,
                [IO.FileShare]::None)
        } catch {
            if (-not (Test-SharingViolation $_)) { throw }
            Start-Sleep -Milliseconds 100
        }
    }
    return $null
}

function ConvertTo-Request {
    # Parses a request's text ("stop" or "restart", then an optional id) and logs it.
    param([string]$Text)
    $parts = @($Text.Trim() -split '\s+' | Where-Object { $_ })
    $verb = ''
    if ($parts.Count -ge 1) { $verb = $parts[0].ToLowerInvariant() }
    $accepted = (@('stop', 'restart') -contains $verb) -and $parts.Count -le 2
    $fields = [ordered]@{}
    if ($accepted) {
        $fields.text = $verb
    } else {
        $shown = $Text.Trim()
        if ($shown.Length -gt 64) { $shown = $shown.Substring(0, 64) }
        $fields.text = $shown
    }
    $fields.accepted = $accepted
    $id = $null
    if ($accepted -and $parts.Count -eq 2) {
        $id = $parts[1]
        if ($id.Length -gt 64) { $id = $id.Substring(0, 64) }
        $fields.id = $id
    }
    Write-SupervisorEvent 'request' $fields
    return @{ verb = $verb; accepted = $accepted; id = $id }
}

function Receive-Request {
    # The pending one-shot request, claimed by renaming it, or $null.
    if (-not [IO.File]::Exists($RequestPath)) { return $null }
    $claimed = "$RequestPath.$PID.claimed"
    try {
        if ([IO.File]::Exists($claimed)) { [IO.File]::Delete($claimed) }
        [IO.File]::Move($RequestPath, $claimed)
    } catch {
        return $null
    }
    $text = ''
    try {
        $text = [IO.File]::ReadAllText($claimed, $Utf8NoBom)
    } catch {
        $text = ''
    } finally {
        try { [IO.File]::Delete($claimed) } catch { }
    }
    return ConvertTo-Request $text
}

# --- The child and its job ----------------------------------------------------------------------

$script:Job = [IntPtr]::Zero
$script:Child = $null

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
    $script:Job = [NextronRun.Native]::NewJob()
    try {
        # NEXTRON_ENV and NEXTRON_DATA_DIR are set for the child only.
        $script:Child = [NextronRun.Native]::Start($script:Job, (Join-Arguments $ServeCommand), $EngineRoot,
            [string[]]@('NEXTRON_ENV', 'NEXTRON_DATA_DIR'), [string[]]@($Env, $DataDir))
    } catch {
        Close-Child
        throw
    }
}

function Close-Child {
    # Closes the child's handles, then the job's: anything still in the job ends with it.
    if ($script:Child) {
        [NextronRun.Native]::Close($script:Child.Thread)
        [NextronRun.Native]::Close($script:Child.Process)
        $script:Child = $null
    }
    if ($script:Job -ne [IntPtr]::Zero) {
        [NextronRun.Native]::Close($script:Job)
        $script:Job = [IntPtr]::Zero
    }
}

function Wait-JobEmpty {
    # Whether the job's last process ends within Milliseconds (counted in slices, not by a clock).
    param([int]$Milliseconds)
    $waited = 0
    while ($true) {
        if ([NextronRun.Native]::ActiveProcesses($script:Job) -eq 0) { return $true }
        if ($waited -ge $Milliseconds) { return $false }
        Start-Sleep -Milliseconds $SliceMilliseconds
        $waited += $SliceMilliseconds
    }
}

function Invoke-Kill {
    # Ends every process in the child's job. Success is judged by the job's active-process count.
    # If the job isn't empty after 10 s, each process still in it is stopped one by one. Each
    # step has its own try: a failed pid listing (for the log) never skips the terminate.
    param([string]$Reason)
    $pids = [int[]]@()
    $ok = $false
    $errors = @()
    $fields = [ordered]@{ reason = $Reason; method = 'job' }
    try { $pids = [int[]][NextronRun.Native]::ProcessIds($script:Job) } catch { $errors += Get-ErrorText $_ }
    $terminated = $false
    if ($script:FailJobKillPending) {
        $script:FailJobKillPending = $false
        $errors += 'TerminateJobObject skipped (-FailJobKillOnce)'
    } else {
        try {
            [NextronRun.Native]::Terminate($script:Job, 1)
            $terminated = $true
        } catch {
            $errors += Get-ErrorText $_
        }
    }
    try {
        if ($terminated) {
            $ok = Wait-JobEmpty $KillWaitMilliseconds
        } else {
            $ok = ([NextronRun.Native]::ActiveProcesses($script:Job) -eq 0)
        }
    } catch {
        $errors += Get-ErrorText $_
    }
    $fields.ok = $ok
    $fields.pids = $pids
    if ($errors) { $fields.error = $errors -join '; ' }
    Write-SupervisorEvent 'kill' $fields
    if ($ok) { return }
    $left = $pids
    try { $left = [int[]][NextronRun.Native]::ProcessIds($script:Job) } catch { $left = $pids }
    $errors = @()
    foreach ($id in $left) {
        try {
            Stop-Process -Id $id -Force -ErrorAction Stop
        } catch {
            if (Get-Process -Id $id -ErrorAction SilentlyContinue) { $errors += "${id}: $(Get-ErrorText $_)" }
        }
    }
    $gone = $false
    try { $gone = Wait-JobEmpty $KillWaitMilliseconds } catch { $errors += Get-ErrorText $_ }
    $fields = [ordered]@{ reason = $Reason; method = 'process_kill'; ok = $gone; pids = $left }
    if ($errors) { $fields.error = $errors -join '; ' }
    Write-SupervisorEvent 'kill' $fields
}

function Stop-ChildGracefully {
    # CTRL_BREAK to the child's process group (Uvicorn shuts down cleanly on it), then up to
    # StopGraceSeconds for the whole job to empty: uv.exe dies at once while the venv launcher
    # waits for Python. Returns whether the job had to be killed.
    $sent = $false
    try { $sent = [NextronRun.Native]::CtrlBreak($script:Child.Id) } catch { $sent = $false }
    if ($sent -and (Wait-JobEmpty ([int][Math]::Round($StopGraceSeconds * 1000)))) { return $false }
    $reason = 'ctrl_break_failed'
    if ($sent) { $reason = 'stop_timeout' }
    Invoke-Kill $reason
    return $true
}

function Get-HeartbeatTicks {
    # The heartbeat's modification time in ticks; a missing file reads as 1601-01-01.
    try {
        return [IO.File]::GetLastWriteTimeUtc($HeartbeatPath).Ticks
    } catch {
        return [long]-1
    }
}

function Wait-Backoff {
    # Sleeps Seconds in slices of a poll, answering requests; returns an accepted request or $null.
    param([double]$Seconds, [int]$PollMilliseconds)
    $total = [int][Math]::Round($Seconds * 1000)
    $waited = 0
    while ($waited -lt $total) {
        $step = [Math]::Min($PollMilliseconds, $total - $waited)
        Start-Sleep -Milliseconds $step
        $waited += $step
        $request = Receive-Request
        if ($request -and $request.accepted) { return $request }
    }
    return $null
}

# --- Modes --------------------------------------------------------------------------------------

function Invoke-Supervisor {
    [void][IO.Directory]::CreateDirectory($LogDir)
    $lock = Open-SupervisorLock
    if (-not $lock) {
        Write-SupervisorEvent 'supervisor_refused' ([ordered]@{ reason = 'lock_held'; lock = $LockPath })
        [Console]::Error.WriteLine("run.ps1: a supervisor is already running for $Env on $DataDir")
        return 3
    }
    $lockTakenAt = [DateTime]::UtcNow
    $script:OwnsLog = $true
    $exitCode = 0
    try {
        if (-not $ServeCommand) {
            $uv = Get-Command 'uv.exe' -CommandType Application -ErrorAction SilentlyContinue |
                Select-Object -First 1
            $uvPath = if ($uv) { $uv.Path } else { 'uv.exe' }
            $script:ServeCommand = [string[]]@($uvPath, 'run', '--frozen', 'python', '-m', 'nextron', 'serve')
        }
        Write-SupervisorEvent 'supervisor_start' ([ordered]@{
                env               = $Env
                data_dir          = $DataDir
                working_dir       = $EngineRoot
                command           = [string[]]$ServeCommand
                backoff_start_s   = $BackoffStartSeconds
                backoff_max_s     = $BackoffMaxSeconds
                reset_after_s     = $ResetAfterSeconds
                stale_after_s     = $StaleAfterSeconds
                start_grace_s     = $StartGraceSeconds
                poll_s            = $PollSeconds
                stop_grace_s      = $StopGraceSeconds
                request_timeout_s = $RequestTimeoutSeconds
                max_restarts      = $MaxRestarts
                fail_job_kill_once = $FailJobKillOnce.IsPresent
            })
        # A request left from before this supervisor took the lock is discarded; one written
        # since (a client saw this supervisor running) is answered at the first poll.
        if ([IO.File]::Exists($RequestPath) -and
            [IO.File]::GetLastWriteTimeUtc($RequestPath) -lt $lockTakenAt) {
            $shown = "$(Read-SharedText $RequestPath)".Trim()
            if ($shown.Length -gt 64) { $shown = $shown.Substring(0, 64) }
            $discarded = [ordered]@{ text = $shown }
            try { [IO.File]::Delete($RequestPath) } catch { $discarded.error = Get-ErrorText $_ }
            Write-SupervisorEvent 'request_discarded' $discarded
        }
        Add-NativeType
        $pollMilliseconds = [int][Math]::Max(1, [Math]::Round($PollSeconds * 1000))
        $backoffStart = [Math]::Min($BackoffStartSeconds, $BackoffMaxSeconds)
        $backoff = $backoffStart
        $restarts = 0
        $attempt = 0
        $exit2Count = 0
        while ($true) {
            $attempt++
            $requested = $null
            $killed = $false
            $started = $false
            $upSeconds = $null
            $childCode = $null
            $lastBeat = Get-HeartbeatTicks
            $clock = [Diagnostics.Stopwatch]::StartNew()
            try {
                Start-Child
                $started = $true
            } catch {
                Write-SupervisorEvent 'start_failed' ([ordered]@{ attempt = $attempt; error = Get-ErrorText $_ })
            }
            if ($started) {
                $childPid = $script:Child.Id
                Write-SupervisorEvent 'start' ([ordered]@{ attempt = $attempt; pid = $childPid })
                $firstBeatAt = $null
                $quietPolls = 0
                while (-not [NextronRun.Native]::Wait($script:Child.Process, $pollMilliseconds)) {
                    $request = Receive-Request
                    if ($request -and $request.accepted) {
                        $requested = $request.verb
                        $killed = Stop-ChildGracefully
                        break
                    }
                    $beat = Get-HeartbeatTicks
                    if ($beat -ne $lastBeat) {
                        $lastBeat = $beat
                        if ($null -eq $firstBeatAt) { $firstBeatAt = $clock.Elapsed.TotalSeconds }
                        $quietPolls = 0
                        continue
                    }
                    $quietPolls++
                    $quiet = $quietPolls * $PollSeconds
                    $limit = if ($null -ne $firstBeatAt) { $StaleAfterSeconds } else { $StartGraceSeconds }
                    if ($quiet -ge $limit - 1e-9) {
                        Write-SupervisorEvent 'stale' ([ordered]@{
                                pid               = $childPid
                                quiet_s           = [Math]::Round($quiet, 3)
                                limit_s           = $limit
                                before_first_beat = ($null -eq $firstBeatAt)
                            })
                        Invoke-Kill 'stale'
                        $killed = $true
                        break
                    }
                }
                if (-not $killed -and -not $requested -and -not (Wait-JobEmpty $OrphanWaitMilliseconds)) {
                    # The child ended but left processes in its job (uv gone, a Python still running).
                    Invoke-Kill 'orphans'
                }
                [void][NextronRun.Native]::Wait($script:Child.Process, -1)
                $childCode = [NextronRun.Native]::ExitCode($script:Child.Process)
                $uptime = $clock.Elapsed.TotalSeconds
                if ($null -ne $firstBeatAt) { $upSeconds = [Math]::Round($uptime - $firstBeatAt, 3) }
                Write-SupervisorEvent 'exit' ([ordered]@{
                        pid       = $childPid
                        code      = $childCode
                        uptime_s  = [Math]::Round($uptime, 3)
                        up_s      = $upSeconds
                        killed    = $killed
                        requested = [bool]$requested
                    })
                Close-Child
            }
            if ($requested -eq 'stop') {
                Write-SupervisorEvent 'stop' ([ordered]@{ code = 0; reason = 'requested' })
                break
            }
            if ($requested -eq 'restart') {
                $backoff = $backoffStart
                $exit2Count = 0
                continue
            }
            if ($started -and -not $killed -and $childCode -eq 3) {
                Write-SupervisorEvent 'stop' ([ordered]@{ code = 3; reason = 'lock_held' })
                $exitCode = 3
                break
            }
            if ($started -and -not $killed -and $childCode -eq 2) {
                $exit2Count++
                if ($exit2Count -ge $Exit2Limit) {
                    Write-SupervisorEvent 'stop' ([ordered]@{ code = 2; reason = 'repeated_exit_2'; count = $exit2Count })
                    $exitCode = 2
                    break
                }
            } else {
                $exit2Count = 0
            }
            if ($MaxRestarts -ge 0 -and $restarts -ge $MaxRestarts) {
                Write-SupervisorEvent 'max_restarts' ([ordered]@{ restarts = $restarts })
                break
            }
            if ($null -ne $upSeconds -and $upSeconds -ge $ResetAfterSeconds) { $backoff = $backoffStart }
            Write-SupervisorEvent 'backoff' ([ordered]@{ wait_s = $backoff })
            $request = Wait-Backoff $backoff $pollMilliseconds
            $restarts++
            if ($request -and $request.verb -eq 'stop') {
                Write-SupervisorEvent 'stop' ([ordered]@{ code = 0; reason = 'requested' })
                break
            }
            if ($request -and $request.verb -eq 'restart') {
                $backoff = $backoffStart
                $exit2Count = 0
                continue
            }
            $backoff = [Math]::Min($backoff * 2, $BackoffMaxSeconds)
        }
    } catch {
        Write-SupervisorEvent 'supervisor_error' ([ordered]@{ error = Get-ErrorText $_ })
        $exitCode = 1
    } finally {
        try {
            if ($script:Child -and [NextronRun.Native]::ActiveProcesses($script:Job) -gt 0) {
                [void](Stop-ChildGracefully)
            }
        } catch {
            Write-SupervisorEvent 'supervisor_error' ([ordered]@{ error = Get-ErrorText $_ })
        }
        try { Close-Child } catch { }
        $lock.Dispose()
    }
    return $exitCode
}

function Remove-OwnRequest {
    # Deletes a request nobody claimed, so it isn't obeyed later by surprise.
    param([string]$Text)
    try {
        if ([IO.File]::Exists($RequestPath) -and [IO.File]::ReadAllText($RequestPath).Trim() -eq $Text) {
            [IO.File]::Delete($RequestPath)
        }
    } catch {
        return
    }
}

function Get-RestartOutcome {
    # 'started <pid>' once a start follows this request in the log, 'failed' once a start_failed
    # or a stop does, else $null.
    param([string]$Id)
    $lines = Get-LogLines
    $needle = '"id":"' + $Id + '"'
    $index = -1
    for ($i = 0; $i -lt $lines.Length; $i++) {
        if ($lines[$i].Contains($needle)) { $index = $i; break }
    }
    if ($index -lt 0) { return $null }
    for ($i = $index + 1; $i -lt $lines.Length; $i++) {
        try {
            $record = $lines[$i] | ConvertFrom-Json
            $name = $record.event
        } catch {
            continue
        }
        if ($name -eq 'start') {
            $startedPid = '?'
            try { $startedPid = $record.pid } catch { $startedPid = '?' }
            return "started $startedPid"
        }
        if ($name -eq 'start_failed' -or $name -eq 'stop') { return 'failed' }
    }
    return $null
}

function Send-Request {
    param([string]$Verb)
    if (-not (Test-SupervisorRunning)) {
        [Console]::Error.WriteLine("run.ps1: no supervisor is running for $Env on $DataDir; nothing was sent")
        return 1
    }
    $text = "$Verb $([Guid]::NewGuid().ToString('N'))"
    $id = $text.Split(' ')[1]
    $temporary = "$RequestPath.$PID.tmp"
    [IO.File]::WriteAllText($temporary, $text, $Utf8NoBom)
    try {
        try { [IO.File]::Delete($RequestPath) } catch { }
        [IO.File]::Move($temporary, $RequestPath)
    } catch {
        try { [IO.File]::Delete($temporary) } catch { }
        [Console]::Error.WriteLine("run.ps1: could not leave the request in ${RequestPath}: $(Get-ErrorText $_)")
        return 1
    }
    $limit = [int][Math]::Round($RequestTimeoutSeconds * 1000)
    $waited = 0
    while ($true) {
        if ($Verb -eq 'stop') {
            if (-not (Test-SupervisorRunning)) {
                [Console]::Out.WriteLine("stopped: the supervisor for $Env and its engine have stopped")
                return 0
            }
        } else {
            $outcome = Get-RestartOutcome $id
            if ($outcome -like 'started *') {
                $newPid = $outcome.Substring(8)
                [Console]::Out.WriteLine("restarted: the supervisor for $Env started a new engine process (pid $newPid); run.ps1 -Env $Env -Status shows its heartbeat")
                return 0
            }
            if ($outcome -eq 'failed') {
                [Console]::Error.WriteLine("run.ps1: the engine stopped but did not start again; see $LogPath")
                return 1
            }
            if (-not (Test-SupervisorRunning)) {
                [Console]::Error.WriteLine("run.ps1: the supervisor for $Env stopped instead of restarting; see $LogPath")
                return 1
            }
        }
        if ($waited -ge $limit) { break }
        Start-Sleep -Milliseconds 250
        $waited += 250
    }
    Remove-OwnRequest $text
    [Console]::Error.WriteLine("run.ps1: no answer from the supervisor for $Env within $RequestTimeoutSeconds s; see $LogPath")
    return 1
}

function Show-Status {
    $running = Test-SupervisorRunning
    $state = if ($running) { 'running' } else { 'not running' }
    [Console]::Out.WriteLine("Supervisor : $state for $Env")
    [Console]::Out.WriteLine("Data dir   : $DataDir")
    if ([IO.File]::Exists($HeartbeatPath)) {
        $age = ([DateTime]::UtcNow - [IO.File]::GetLastWriteTimeUtc($HeartbeatPath)).TotalSeconds
        [Console]::Out.WriteLine("Heartbeat  : last beat $([Math]::Round($age, 1).ToString($Invariant)) s ago")
    } else {
        [Console]::Out.WriteLine('Heartbeat  : no file yet')
    }
    [Console]::Out.WriteLine("Log        : $LogPath")
    $lines = Get-LogLines
    if ($lines.Length -eq 0) {
        [Console]::Out.WriteLine('--- no supervisor log yet')
        return
    }
    [Console]::Out.WriteLine('--- the last 15 lines of the supervisor log')
    $first = [Math]::Max(0, $lines.Length - 15)
    for ($i = $first; $i -lt $lines.Length; $i++) { [Console]::Out.WriteLine($lines[$i]) }
}

# --- Main ---------------------------------------------------------------------------------------

$modeCount = @($Stop, $Restart, $Status | Where-Object { $_.IsPresent }).Count
if ($modeCount -gt 1) {
    [Console]::Error.WriteLine('run.ps1: use at most one of -Stop, -Restart and -Status')
    exit 2
}
if ($Status) {
    Show-Status
    exit 0
}
if ($Stop) { exit (Send-Request 'stop') }
if ($Restart) { exit (Send-Request 'restart') }
exit (Invoke-Supervisor)
