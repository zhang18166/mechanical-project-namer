param([Parameter(Mandatory)][string]$Ready)
$ErrorActionPreference='Stop'
$readyPath=(Resolve-Path -LiteralPath $Ready).Path
$info=Get-Content -LiteralPath $readyPath -Raw | ConvertFrom-Json
if (-not $info.accounts -or [int]$info.pid -le 1) { throw 'Invalid account service receipt.' }
$taskProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$info.pid)"
if (-not $taskProcess) { return }
if (-not $taskProcess.CommandLine.Contains('mechanical_project_namer.account_server') -or -not $taskProcess.CommandLine.Contains($readyPath)) {
    throw 'Process ownership mismatch; not stopped.'
}
Stop-Process -Id $taskProcess.ProcessId
