param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$')]
    [string]$TargetTime,

    [string]$TaskName = 'SwimTicketAssistant',

    [ValidateRange(15, 3600)]
    [int]$WakeLeadSeconds = 60
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$SchedulerPath = Join-Path $ProjectRoot 'scheduler.py'

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Virtual environment Python was not found: $PythonPath. Run uv sync first."
}
if (-not (Test-Path -LiteralPath $SchedulerPath -PathType Leaf)) {
    throw "Scheduler script was not found: $SchedulerPath"
}

$UvCommand = Get-Command uv -ErrorAction Stop
$UvPath = $UvCommand.Source
if (-not (Test-Path -LiteralPath $UvPath -PathType Leaf)) {
    throw "uv.exe was not found: $UvPath"
}

$Culture = [System.Globalization.CultureInfo]::InvariantCulture
$TargetDateTime = [datetime]::ParseExact($TargetTime, 'HH:mm:ss', $Culture)
$TriggerAt = [datetime]::Today.Add($TargetDateTime.TimeOfDay).AddSeconds(-$WakeLeadSeconds)

$Arguments = @(
    ('"{0}"' -f $SchedulerPath)
    '--target-time'
    $TargetTime
    '--wake-lead-seconds'
    $WakeLeadSeconds
    '--uv-path'
    ('"{0}"' -f $UvPath)
) -join ' '

$Action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument $Arguments `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -Daily -At $TriggerAt
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited

$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$Task = New-ScheduledTask `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Start the scheduler $WakeLeadSeconds seconds early so main.py starts near $TargetTime each day."

Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null

Write-Host "Scheduled task created or updated: $TaskName"
Write-Host "main.py target start time: $TargetTime"
Write-Host "Daily scheduler trigger time: $($TriggerAt.ToString('HH:mm:ss'))"
Write-Host "Project directory: $ProjectRoot"
Write-Host "Log directory: $(Join-Path $ProjectRoot 'logs')"
Write-Host 'Keep the user logged in, desktop unlocked, WeChat logged in, and the swim chat open.'
