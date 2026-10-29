param([string]$Profile = '', [string]$SessionRoot = '')
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$python=Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project .venv missing; see README.' }
$arguments=@('-m','mechanical_project_namer.backup_entry')
if ($Profile) { $arguments+=@('--profile',$Profile) }
if ($SessionRoot) { $arguments+=@('--session-root',$SessionRoot) }
Push-Location -LiteralPath $projectRoot
try { & $python @arguments; $result=$LASTEXITCODE } finally { Pop-Location }
exit $result
