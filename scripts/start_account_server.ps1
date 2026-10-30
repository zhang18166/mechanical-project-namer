param([Parameter(Mandatory)][string]$Config,[Parameter(Mandatory)][string]$Ready,[int]$Port=0)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    & "$projectRoot\.venv\Scripts\python.exe" -m mechanical_project_namer.account_server serve --config $Config --ready $Ready --port $Port
    $result=$LASTEXITCODE
} finally { Pop-Location }
exit $result
