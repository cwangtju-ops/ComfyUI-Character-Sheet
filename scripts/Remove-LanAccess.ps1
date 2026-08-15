[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$RuleName = 'Expression Wizard LAN'
)

$ErrorActionPreference = 'Stop'
$ruleName = $RuleName
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from PowerShell opened with Run as administrator.'
}

$rule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($rule) {
    $rule | Remove-NetFirewallRule
    Write-Host 'Expression Wizard firewall access was removed.' -ForegroundColor Green
} else {
    Write-Host 'No Expression Wizard firewall rule exists.'
}

