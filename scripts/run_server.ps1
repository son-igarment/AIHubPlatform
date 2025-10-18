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

$VenvPy = Join-Path $Root '.venv/Scripts/python.exe'
$Activate = Join-Path $Root '.venv/Scripts/Activate.ps1'
if (-not (Test-Path $VenvPy) -and -not (Test-Path $Activate)) {
  Write-Host 'Python venv not found. Bootstrapping via scripts/setup_python.ps1'
  & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'setup_python.ps1')
}

# Prefer venv python if available; fall back to system python
$PythonExe = if (Test-Path $VenvPy) { $VenvPy } else { 'python' }

if (Test-Path $Activate) {
  Write-Host 'Activating venv...'
  . $Activate
}

Write-Host "Starting Uvicorn: app.main:app with $PythonExe on 0.0.0.0:8000"
& $PythonExe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

