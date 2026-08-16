param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Laptop', 'Gateway')]
    [string]$Component,

    [Parameter(Mandatory = $true)]
    [ValidateSet('Start', 'Stop', 'Status', 'OpenLogs')]
    [string]$Action,

    [switch]$OpenBrowser,
    [switch]$CancelActive,
    [string]$ResultFile
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$userRoot = Join-Path $env:USERPROFILE '.expression_wizard'
$runtimeRoot = Join-Path $userRoot ('runtime\' + $Component.ToLowerInvariant())
$configFile = Join-Path $userRoot 'service.json'
$pidFile = Join-Path $runtimeRoot 'process.json'
$stdoutLog = Join-Path $runtimeRoot 'stdout.log'
$stderrLog = Join-Path $runtimeRoot 'stderr.log'
$controlTokenFile = Join-Path $runtimeRoot 'control_token.txt'
$script:resultLines = [System.Collections.Generic.List[string]]::new()

function Write-Result([string]$Message) {
    $script:resultLines.Add($Message)
    Write-Output $Message
}

function Complete-Result([int]$ExitCode) {
    if ($ResultFile) {
        $parent = Split-Path -Parent $ResultFile
        if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        [IO.File]::WriteAllLines($ResultFile, $script:resultLines, [Text.UTF8Encoding]::new($false))
    }
    exit $ExitCode
}

function Get-PropertyValue($Object, [string]$Name, $Default) {
    if ($null -ne $Object -and $Object.PSObject.Properties.Name -contains $Name) {
        $value = $Object.$Name
        if ($null -ne $value -and "$value" -ne '') { return $value }
    }
    return $Default
}

function Get-ServiceConfig {
    $config = $null
    if (Test-Path -LiteralPath $configFile) {
        $config = Get-Content -Raw -LiteralPath $configFile | ConvertFrom-Json
    }
    $sectionName = $Component.ToLowerInvariant()
    if ($null -ne $config -and $config.PSObject.Properties.Name -contains $sectionName) {
        return $config.$sectionName
    }
    return $null
}

function Quote-Argument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Rotate-Log([string]$Path) {
    if ((Test-Path -LiteralPath $Path) -and (Get-Item -LiteralPath $Path).Length -gt 5MB) {
        $archive = $Path + '.1'
        if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
        Move-Item -LiteralPath $Path -Destination $archive
    }
}

function Ensure-ControlToken {
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    if (-not (Test-Path -LiteralPath $controlTokenFile)) {
        $token = ([guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))
        [IO.File]::WriteAllText($controlTokenFile, $token + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    }
    $token = (Get-Content -Raw -LiteralPath $controlTokenFile).Trim()
    if ($token.Length -lt 24) { throw 'Lifecycle control token is invalid.' }
    return $token
}

function Get-ExpectedService([string]$HealthUrl) {
    try {
        return Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
    } catch {
        return $null
    }
}

function Get-TrackedProcess {
    if (-not (Test-Path -LiteralPath $pidFile)) { return $null }
    try {
        $record = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
        $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
        if (-not $process) { return $null }
        if ($record.PSObject.Properties.Name -contains 'process_start_time') {
            $expectedStart = ([datetime]$record.process_start_time).ToUniversalTime()
            $difference = [Math]::Abs(($process.StartTime.ToUniversalTime() - $expectedStart).TotalSeconds)
            if ($difference -gt 1.0) { return $null }
        }
        if ($record.PSObject.Properties.Name -contains 'executable') {
            $actualExecutable = $process.Path
            if ($actualExecutable -and [IO.Path]::GetFullPath($actualExecutable) -ne [IO.Path]::GetFullPath([string]$record.executable)) { return $null }
        }
        return [pscustomobject]@{ Record = $record; Process = $process }
    } catch {}
    return $null
}

function Open-Url([string]$Url) {
    Start-Process $Url | Out-Null
}

function Invoke-Lifecycle([string]$BaseUrl, [string]$Token, [string]$Path, $Body) {
    $headers = @{ Authorization = "Bearer $Token" }
    return Invoke-RestMethod -Uri ($BaseUrl + $Path) -Method Post -Headers $headers `
        -ContentType 'application/json' -Body ($Body | ConvertTo-Json -Compress) -TimeoutSec 10
}

try {
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    $settings = Get-ServiceConfig

    if ($Component -eq 'Laptop') {
        $defaultPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        $python = [string](Get-PropertyValue $settings 'python' $(if ($env:EXPRESSION_WIZARD_DEV_PYTHON) { $env:EXPRESSION_WIZARD_DEV_PYTHON } elseif ($env:EXPRESSION_WIZARD_PYTHON) { $env:EXPRESSION_WIZARD_PYTHON } else { $defaultPython }))
        $dataRoot = [string](Get-PropertyValue $settings 'data_root' $(if ($env:EXPRESSION_WIZARD_DATA_ROOT) { $env:EXPRESSION_WIZARD_DATA_ROOT } else { 'C:\Codex Projects\ComfyUI\Character Sheet_Lys' }))
        $gatewayUrl = [string](Get-PropertyValue $settings 'gateway_url' $(if ($env:EXPRESSION_WIZARD_COMFY_URL) { $env:EXPRESSION_WIZARD_COMFY_URL } else { 'http://192.168.2.200:8189' }))
        $port = [int](Get-PropertyValue $settings 'port' $(if ($env:EXPRESSION_WIZARD_LOCAL_PORT) { $env:EXPRESSION_WIZARD_LOCAL_PORT } else { 8775 }))
        $idleTimeout = [int](Get-PropertyValue $settings 'idle_timeout_seconds' 0)
        $baseUrl = "http://127.0.0.1:$port"
        $healthUrl = "$baseUrl/api/explore/health"
        $expectedService = 'Expression Wizard'
        $scriptPath = Join-Path $repository 'ComfyUI_Workflows\expression_wizard\server.py'
        $generationTokenFile = [string](Get-PropertyValue $settings 'generation_token_file' (Join-Path $userRoot 'desktop_comfy_gateway_token.txt'))
        $adminTokenFile = [string](Get-PropertyValue $settings 'admin_token_file' (Join-Path $userRoot 'desktop_comfy_gateway_admin_token.txt'))
        foreach ($required in @($python, $scriptPath, $dataRoot, $generationTokenFile)) {
            if (-not (Test-Path -LiteralPath $required)) { throw "Required path is missing: $required" }
        }
        $env:EXPRESSION_WIZARD_COMFY_URL = $gatewayUrl
        $env:EXPRESSION_WIZARD_COMFY_TOKEN = (Get-Content -Raw -LiteralPath $generationTokenFile).Trim()
        if (Test-Path -LiteralPath $adminTokenFile) {
            $env:EXPRESSION_WIZARD_COMFY_ADMIN_TOKEN = (Get-Content -Raw -LiteralPath $adminTokenFile).Trim()
        }
        $arguments = @($scriptPath, '--host', '127.0.0.1', '--port', "$port", '--comfy-gateway', $gatewayUrl, '--data-root', $dataRoot, '--control-token-file', $controlTokenFile, '--idle-timeout', "$idleTimeout")
        $browserUrl = "$baseUrl/"
    } else {
        $python = [string](Get-PropertyValue $settings 'python' $(if ($env:EXPRESSION_WIZARD_PYTHON) { $env:EXPRESSION_WIZARD_PYTHON } else { 'C:\Comfy Powerhouse\Comfy Powerhouse\ComfyUI\.venv\Scripts\python.exe' }))
        $comfyRoot = [string](Get-PropertyValue $settings 'comfy_root' $(if ($env:EXPRESSION_WIZARD_COMFY_ROOT) { $env:EXPRESSION_WIZARD_COMFY_ROOT } else { 'C:\Comfy Powerhouse\Comfy Powerhouse\ComfyUI' }))
        $allowedClients = [string](Get-PropertyValue $settings 'allowed_clients' $(if ($env:EXPRESSION_WIZARD_ALLOWED_CLIENTS) { $env:EXPRESSION_WIZARD_ALLOWED_CLIENTS } else { '192.168.2.242' }))
        $port = [int](Get-PropertyValue $settings 'port' 8189)
        $baseUrl = "http://127.0.0.1:$port"
        $healthUrl = "$baseUrl/health"
        $expectedService = 'Expression Wizard ComfyUI Gateway'
        $scriptPath = Join-Path $repository 'ComfyUI_Workflows\expression_wizard\comfy_gateway.py'
        foreach ($required in @($python, $scriptPath, $comfyRoot)) {
            if (-not (Test-Path -LiteralPath $required)) { throw "Required path is missing: $required" }
        }
        if (-not $allowedClients.Trim()) { throw 'At least one allowed laptop IPv4 address is required.' }
        $arguments = @($scriptPath, '--host', '0.0.0.0', '--port', "$port", '--api', 'http://127.0.0.1:8188', '--comfy-root', $comfyRoot, '--control-token-file', $controlTokenFile)
        foreach ($clientAddress in $allowedClients.Split(',', [StringSplitOptions]::RemoveEmptyEntries)) {
            $arguments += @('--allow-client', $clientAddress.Trim())
        }
        $browserUrl = $null
    }

    $health = Get-ExpectedService $healthUrl
    $isRunning = $null -ne $health -and $health.service -eq $expectedService

    if ($Action -eq 'Status') {
        if ($isRunning) {
            $tracked = Get-TrackedProcess
            $pidText = if ($tracked) { "PID $($tracked.Process.Id)" } else { 'external/untracked process' }
            Write-Result "$Component is running at $baseUrl ($pidText)."
            Complete-Result 0
        }
        $tracked = Get-TrackedProcess
        if ($tracked) {
            Write-Result "$Component process exists (PID $($tracked.Process.Id)) but its health check failed. See $stderrLog"
            Complete-Result 2
        }
        Write-Result "$Component is stopped."
        if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force }
        Complete-Result 1
    }

    if ($Action -eq 'OpenLogs') {
        Start-Process explorer.exe -ArgumentList @('/select,', $stderrLog) | Out-Null
        Write-Result "Opened $runtimeRoot"
        Complete-Result 0
    }

    if ($Action -eq 'Stop') {
        if (-not $isRunning) {
            Write-Result "$Component is already stopped."
            Complete-Result 0
        }
        $token = Ensure-ControlToken
        try {
            Invoke-Lifecycle $baseUrl $token '/api/lifecycle/shutdown' @{ cancel_active = [bool]$CancelActive } | Out-Null
        } catch {
            Write-Result "Could not stop $Component safely: $($_.Exception.Message)"
            Write-Result 'If an experiment is active, finish it first or use -CancelActive.'
            Complete-Result 2
        }
        Write-Result "$Component accepted the graceful shutdown request."
        for ($attempt = 0; $attempt -lt 50; $attempt++) {
            Start-Sleep -Milliseconds 200
            if (-not (Get-ExpectedService $healthUrl)) {
                if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force }
                break
            }
        }
        Complete-Result 0
    }

    if ($isRunning) {
        Write-Result "$Component is already running at $baseUrl."
        if ($OpenBrowser -and $browserUrl) { Open-Url $browserUrl }
        Complete-Result 0
    }

    $tracked = Get-TrackedProcess
    if ($tracked) {
        throw "Tracked PID $($tracked.Process.Id) is alive but $healthUrl did not identify the expected service. Refusing to create a second instance."
    }

    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listener) { throw "Port $port is already occupied by another process." }

    Ensure-ControlToken | Out-Null
    Rotate-Log $stdoutLog
    Rotate-Log $stderrLog
    $argumentLine = ($arguments | ForEach-Object { Quote-Argument "$_" }) -join ' '
    $process = Start-Process -FilePath $python -ArgumentList $argumentLine -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
    $record = [ordered]@{
        component = $Component
        pid = $process.Id
        started_at = [DateTime]::UtcNow.ToString('o')
        process_start_time = $process.StartTime.ToUniversalTime().ToString('o')
        executable = $python
        script = $scriptPath
        port = $port
        repository = $repository
    }
    [IO.File]::WriteAllText($pidFile, ($record | ConvertTo-Json), [Text.UTF8Encoding]::new($false))

    $ready = $false
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        Start-Sleep -Milliseconds 200
        $health = Get-ExpectedService $healthUrl
        if ($null -ne $health -and $health.service -eq $expectedService) { $ready = $true; break }
        if ($process.HasExited) { break }
    }
    if (-not $ready) { throw "$Component did not become healthy. See $stderrLog and $stdoutLog" }
    Write-Result "$Component started silently at $baseUrl (PID $($process.Id))."
    if ($OpenBrowser -and $browserUrl) { Open-Url $browserUrl }
    Complete-Result 0
} catch {
    Write-Result "ERROR: $($_.Exception.Message)"
    Write-Result "Logs: $runtimeRoot"
    Complete-Result 2
}
