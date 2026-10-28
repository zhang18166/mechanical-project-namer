$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'preview-web')
try {
    npm.cmd ci --ignore-scripts --registry=https://registry.npmjs.org
    if ($LASTEXITCODE -ne 0) { throw 'Locked npm installation failed' }
    npm.cmd audit --omit=dev
    if ($LASTEXITCODE -ne 0) { throw 'Viewer dependency advisory detected' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Viewer build failed' }
} finally { Pop-Location }
