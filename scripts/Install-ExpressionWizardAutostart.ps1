param(
    [ValidateSet('Install', 'Remove', 'Status')]
    [string]$Action = 'Install',
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$taskName = 'Expression Wizard Managed Gateway'
$legacyTaskName = 'Expression Wizard Comfy Gateway'
$repository = Split-Path -Parent $PSScriptRoot
$manager = Join-Path $repository 'scripts\Manage-ExpressionWizard.ps1'
$userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$backupRoot = Join-Path $env:USERPROFILE '.expression_wizard\backups'

function Get-Task {
    return Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

if ($Action -eq 'Status') {
    $task = Get-Task
    if (-not $task) {
        Write-Output 'Expression Wizard desktop autostart is not installed.'
        exit 1
    }
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    Write-Output "Expression Wizard desktop autostart is installed for $userId."
    Write-Output "Task state: $($task.State)"
    Write-Output "Last result: $($info.LastTaskResult)"
    Write-Output "Last run: $($info.LastRunTime)"
    Write-Output "Next run: $($info.NextRunTime)"
    exit 0
}

if ($Action -eq 'Remove') {
    if (Get-Task) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output 'Expression Wizard desktop autostart was removed. Running services were not stopped.'
    } else {
        Write-Output 'Expression Wizard desktop autostart was already absent.'
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $manager)) {
    throw "Expression Wizard manager is missing: $manager"
}

$legacy = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
if ($legacy -and ($legacy.Actions | Where-Object { $_.Arguments -match 'expression_wizard[\\/]comfy_gateway\.py' })) {
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backupPath = Join-Path $backupRoot "legacy_gateway_task_$stamp.xml"
    Export-ScheduledTask -TaskName $legacyTaskName | Set-Content -LiteralPath $backupPath -Encoding UTF8
    Disable-ScheduledTask -TaskName $legacyTaskName | Out-Null
    Write-Output "The legacy direct-Gateway task was backed up to $backupPath and disabled."
}

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $manager + '" -Component Gateway -Action Start'
$taskAction = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $repository
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$trigger.Delay = 'PT15S'
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

$task = New-ScheduledTask -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Starts Comfy Desktop when needed, waits for ComfyUI, then starts the authenticated Expression Wizard Gateway.'
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force -ErrorAction Stop | Out-Null
Write-Output "Expression Wizard desktop autostart was installed for $userId."

if (-not $NoStart) {
    Start-ScheduledTask -TaskName $taskName
    Write-Output 'The task was started once for verification.'
}
