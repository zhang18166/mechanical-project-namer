param(
    [string]$DevRoot = '',
    [switch]$Smoke
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project .venv missing. See docs/workbench/BASELINE.md; do not upgrade global Python.'
}
$arguments = @('-m', 'mechanical_project_namer', '--dev')
if ($DevRoot) { $arguments += @('--dev-root', $DevRoot) }
if ($Smoke) { $arguments += '--dev-smoke' }
Push-Location -LiteralPath $projectRoot
try {
    & $python @arguments
    $result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $result
