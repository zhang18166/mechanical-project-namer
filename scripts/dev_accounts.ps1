param([string]$DevRoot='', [switch]$Smoke)
$projectRoot=Split-Path -Parent $PSScriptRoot
if (-not $DevRoot) { $DevRoot=Join-Path (Split-Path -Parent $projectRoot) ('_dev_data\'+(Split-Path -Leaf $projectRoot)) }
& (Join-Path $PSScriptRoot 'dev.ps1') -DevRoot $DevRoot -Smoke:$Smoke
exit $LASTEXITCODE
