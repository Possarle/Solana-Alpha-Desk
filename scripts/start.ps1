$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (!(Test-Path .env)) { Copy-Item .env.example .env }
if (!(Test-Path .venv)) { py -3.12 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar backend" }
Push-Location frontend
npm ci
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar frontend" }
npm run build
if ($LASTEXITCODE -ne 0) { throw "Falha no build" }
Pop-Location
Get-Content .env | ForEach-Object {
  if ($_ -match '^([A-Z_]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
}
Set-Location backend
& ..\.venv\Scripts\alembic.exe upgrade head
if ($LASTEXITCODE -ne 0) { throw "Falha na migração" }
& ..\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
