$ErrorActionPreference = 'Stop'

# Config
$ToolsDir = Join-Path $PSScriptRoot '..' | Join-Path -ChildPath 'tools'
$null = New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
$UvExe = Join-Path $ToolsDir 'uv.exe'
$VenvDir = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv'

function Download-Uv {
  if (Test-Path $UvExe) { return }
  Write-Host 'Downloading uv (Python+pip manager)…'
  $url = 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.exe'
  try {
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $UvExe -TimeoutSec 120
  } catch {
    Write-Warning 'Direct download failed, trying installer script from astral.sh'
    $installer = Join-Path $ToolsDir 'install-uv.ps1'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $installer -TimeoutSec 120
    & powershell -ExecutionPolicy Bypass -File $installer
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { Copy-Item $cmd.Source $UvExe -Force }
    if (-not (Test-Path $UvExe)) {
      $userUv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
      if (Test-Path $userUv) { Copy-Item $userUv $UvExe -Force }
    }
  }
  if (-not (Test-Path $UvExe)) { throw 'Failed to acquire uv.exe' }
}

function Ensure-Venv {
  & $UvExe venv $VenvDir | Out-Host
}

function Install-Requirements {
  $req = Join-Path (Split-Path $PSScriptRoot -Parent) 'requirements.txt'
  if (-not (Test-Path $req)) { throw "requirements.txt not found at $req" }
  & $UvExe pip install -r $req -p $VenvDir | Out-Host
}

Download-Uv
Ensure-Venv
Install-Requirements

Write-Host "\nDone. Activate the environment with:"
Write-Host "  $($VenvDir)\\Scripts\\Activate.ps1"
Write-Host "Then check: python -V  and  pip -V"
