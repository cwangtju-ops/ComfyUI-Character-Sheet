[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({
        $parsed = $null
        [System.Net.IPAddress]::TryParse($_, [ref]$parsed) -and $parsed.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork
    })]
    [string]$LaptopAddress,

    [ValidateRange(1024, 65535)]
    [int]$Port = 8765,

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
if (-not $rule) {
    $rule = New-NetFirewallRule `
        -DisplayName $ruleName `
        -Description 'Allow one laptop to access Expression Wizard on a private network.' `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -RemoteAddress $LaptopAddress `
        -Profile Private
} else {
    $rule | Set-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -Profile Private
    $rule | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol TCP -LocalPort $Port
    $rule | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress $LaptopAddress
}

Write-Host "Expression Wizard firewall access is enabled." -ForegroundColor Green
Write-Host "Laptop IPv4: $LaptopAddress"
Write-Host "Desktop port: $Port"
Write-Host 'Profile: Private only'

