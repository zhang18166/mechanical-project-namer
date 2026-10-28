param(
    [string]$DevRoot = (Join-Path $env:LOCALAPPDATA 'MechanicalWorkbenchDev\phase2a'),
    [switch]$Smoke
)
# A distinct profile also isolates its single-instance identity from older dev builds.
& (Join-Path $PSScriptRoot 'dev.ps1') -DevRoot $DevRoot -Smoke:$Smoke
exit $LASTEXITCODE
