$ErrorActionPreference = 'Stop'

$Root = Split-Path $PSScriptRoot -Parent
$EnvFile = Join-Path $Root '.env'
if (Test-Path $EnvFile) {
  Write-Host "Loading environment from .env"
  Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^(#|\s*$)') { return }
    $key,$val = $_.Split('=',2)
    if ($key -and $val) { [System.Environment]::SetEnvironmentVariable($key.Trim(), $val.Trim()) }
  }
}

if (-not (Test-Path "$Root/.venv/Scripts/Activate.ps1")) {
  Write-Host 'Python venv not found. Bootstrapping via scripts/setup_python.ps1'
  & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'setup_python.ps1')
}

. "$Root/.venv/Scripts/Activate.ps1"

Write-Host 'Starting Uvicorn: app.main:app on 0.0.0.0:8000'
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

