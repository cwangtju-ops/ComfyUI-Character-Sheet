param(
    [string]$Repository = 'C:\Codex Projects\ComfyUI Character Sheet',
    [string]$DataRoot = 'C:\Codex Projects\ComfyUI\Character Sheet_Lys',
    [string]$GatewayUrl = 'http://192.168.2.200:8189',
    [int]$Port = 8775
)

$ErrorActionPreference = 'Stop'

$python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$server = Join-Path $Repository 'ComfyUI_Workflows\expression_wizard\server.py'
$tokenRoot = Join-Path $env:USERPROFILE '.expression_wizard'
$generationTokenFile = Join-Path $tokenRoot 'desktop_comfy_gateway_token.txt'
$adminTokenFile = Join-Path $tokenRoot 'desktop_comfy_gateway_admin_token.txt'

foreach ($required in @($python, $server, $DataRoot, $generationTokenFile, $adminTokenFile)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required Expression Wizard path is missing: $required"
    }
}

$env:EXPRESSION_WIZARD_COMFY_URL = $GatewayUrl
$env:EXPRESSION_WIZARD_COMFY_TOKEN = (Get-Content -Raw -LiteralPath $generationTokenFile).Trim()
$env:EXPRESSION_WIZARD_COMFY_ADMIN_TOKEN = (Get-Content -Raw -LiteralPath $adminTokenFile).Trim()
$env:EXPRESSION_WIZARD_DATA_ROOT = $DataRoot

if ($env:EXPRESSION_WIZARD_COMFY_TOKEN.Length -lt 24 -or $env:EXPRESSION_WIZARD_COMFY_ADMIN_TOKEN.Length -lt 24) {
    throw 'A desktop ComfyUI gateway token file is invalid.'
}

& $python $server `
    --host 127.0.0.1 `
    --port $Port `
    --comfy-gateway $GatewayUrl `
    --data-root $DataRoot

exit $LASTEXITCODE
