param(
    [string]$Repository = 'C:\Codex Projects\ComfyUI Character Sheet',
    [string]$DataRoot = 'C:\Codex Projects\ComfyUI\Character Sheet_Lys',
    [string]$GatewayUrl = 'http://192.168.2.200:8189',
    [int]$Port = 8775
)

$ErrorActionPreference = 'Stop'

$manager = Join-Path $Repository 'scripts\Manage-ExpressionWizard.ps1'
if (-not (Test-Path -LiteralPath $manager)) {
    throw "Expression Wizard lifecycle manager is missing: $manager"
}
$env:EXPRESSION_WIZARD_COMFY_URL = $GatewayUrl
$env:EXPRESSION_WIZARD_DATA_ROOT = $DataRoot
$env:EXPRESSION_WIZARD_LOCAL_PORT = "$Port"

& $manager -Component Laptop -Action Start
exit $LASTEXITCODE
