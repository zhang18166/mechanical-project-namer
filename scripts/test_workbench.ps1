param([string[]]$PytestArgs = @())
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project .venv missing.' }
$runName = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
$runDirectory = Join-Path $env:LOCALAPPDATA "MechanicalWorkbenchDev\test-runs\$runName"
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
Push-Location -LiteralPath $projectRoot
try {
    & $python -m pytest --basetemp (Join-Path $runDirectory 'temp') -o "cache_dir=$(Join-Path $runDirectory 'cache')" --junitxml (Join-Path $runDirectory 'junit.xml') @PytestArgs
    $result = $LASTEXITCODE
    Write-Output "Test evidence: $runDirectory"
} finally {
    Pop-Location
}
exit $result
