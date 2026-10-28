param([switch]$WithFixtures)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    & (Join-Path $PSScriptRoot 'setup_preview.ps1')
    & npm ci --prefix cad-web --ignore-scripts --registry=https://registry.npmjs.org
    if ($LASTEXITCODE -ne 0) { throw 'Offline STEP assets installation failed' }
    & .\.venv\Scripts\python.exe -c "from mechanical_project_namer.cad import verify_step_assets;verify_step_assets();print('STEP assets hash verified')"
    if ($LASTEXITCODE -ne 0) { throw 'STEP assets verification failed' }
    if ($WithFixtures) {
        & .\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple -r requirements-pdf-test.lock
        if ($LASTEXITCODE -ne 0) { throw 'PDF fixture tools installation failed' }
        & .\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: --index-url https://pypi.org/simple -r requirements-cad-fixtures.lock
        if ($LASTEXITCODE -ne 0) { throw 'CAD writer fixture tools installation failed' }
    }
    & .\.venv\Scripts\python.exe -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed' }
} finally { Pop-Location }
