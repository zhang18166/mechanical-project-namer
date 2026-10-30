param([Parameter(Mandatory)][string]$Config,[Parameter(Mandatory)][string]$Ready,[int]$Port=0)
$root=Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $root
try { & "$root\.venv\Scripts\python.exe" -m mechanical_project_namer.publish_server --config $Config --ready $Ready --port $Port; $result=$LASTEXITCODE } finally { Pop-Location }
exit $result
