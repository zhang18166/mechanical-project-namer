param([Parameter(Mandatory)][string]$Ready)
$ErrorActionPreference='Stop'
$path=(Resolve-Path -LiteralPath $Ready).Path
$info=Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
$process=Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$info.pid)"
if (-not $process) { return }
if (-not $process.CommandLine.Contains('mechanical_project_namer.publish_server') -or -not $process.CommandLine.Contains($path)) { throw 'Process ownership mismatch; not stopped.' }
Stop-Process -Id $process.ProcessId
