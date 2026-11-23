$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
uv sync --frozen
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
uv run python -m mercury.cli configure
uv run python -m mercury.cli migrate
if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
uv run python -m mercury.cli seed
if ($LASTEXITCODE -ne 0) { throw 'Seed failed' }
Push-Location apps/web
try { npm ci --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed' } } finally { Pop-Location }
New-Item -ItemType Directory -Path '.data' -Force | Out-Null
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$apiProcess = Start-Process -FilePath $pythonExe -ArgumentList '-m','uvicorn','mercury.api:create_app','--factory','--host','127.0.0.1','--port','8040' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput '.data/api.log' -RedirectStandardError '.data/api-error.log'
$workerProcess = Start-Process -FilePath $pythonExe -ArgumentList '-m','mercury.worker' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput '.data/worker.log' -RedirectStandardError '.data/worker-error.log'
$nodeExe = (Get-Command node.exe).Source
$nextEntry = Join-Path $projectRoot 'apps\web\node_modules\next\dist\bin\next'
$webProcess = Start-Process -FilePath $nodeExe -ArgumentList $nextEntry,'dev','--hostname','127.0.0.1','--port','3040' -WorkingDirectory (Join-Path $projectRoot 'apps\web') -WindowStyle Hidden -PassThru -RedirectStandardOutput '.data/web.log' -RedirectStandardError '.data/web-error.log'
@($apiProcess,$workerProcess,$webProcess) | ForEach-Object { @{ id=$_.Id; started=$_.StartTime.ToUniversalTime().ToString('o') } } | ConvertTo-Json | Set-Content -LiteralPath '.data/demo-processes.json'
Write-Host 'MercuryCare: http://127.0.0.1:3040  |  API docs: http://127.0.0.1:8040/docs'
