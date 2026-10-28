$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { py -3.12 -m venv .venv }
    & .\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple -r requirements-preview.lock
    if ($LASTEXITCODE -ne 0) { throw 'Preview dependency installation failed' }
    & .\.venv\Scripts\python.exe -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed' }
    # Reviewed offline JS is committed; rebuilding is optional and separate.
} finally { Pop-Location }
