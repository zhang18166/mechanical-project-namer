param(
    [string]$DevRoot = (Join-Path $env:LOCALAPPDATA 'MechanicalWorkbenchDev\phase2b'),
    [switch]$Smoke
)
& (Join-Path $PSScriptRoot 'dev.ps1') -DevRoot $DevRoot -Smoke:$Smoke
exit $LASTEXITCODE
