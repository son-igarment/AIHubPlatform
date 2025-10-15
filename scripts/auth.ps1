param(
  [Parameter(Mandatory=$true, Position=0)]
  [ValidateSet('register','login','profile-get','profile-update','logout','help')]
  [string]$Command,

  # Common/optional parameters for commands
  [string]$FullName,
  [string]$Email,
  [string]$Password,
  [ValidateSet('Backend_Developer','Lead_Developer','Fullstack_Developer','Founder')]
  [string]$Role,
  [ValidateSet('Backend_Developer','API_Integration_Engineer','Fullstack_Developer','Project_Manager','DevOps_Engineer','QA_Engineer')]
  [string]$Position
)

$ErrorActionPreference = 'Stop'

function Get-BaseUrl {
  if ($env:AIHUB_API_BASE_URL -and $env:AIHUB_API_BASE_URL.Trim()) { return $env:AIHUB_API_BASE_URL }
  return 'https://aihubtasktracker-bwbz.onrender.com'
}

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$TokenFile = Join-Path $ScriptRoot '.token'

function Save-Token([string]$token) {
  Set-Content -Path $TokenFile -Value $token -NoNewline
}

function Load-Token {
  if (Test-Path $TokenFile) { return Get-Content -Path $TokenFile -Raw }
  throw 'No token found. Please login first.'
}

function Clear-Token { if (Test-Path $TokenFile) { Remove-Item $TokenFile -Force } }

function Write-Json([object]$obj) { $obj | ConvertTo-Json -Depth 10 }

function Show-Help {
  @'
Usage:
  ./scripts/auth.ps1 register -FullName "John" -Email "john@example.com" -Password "Passw0rd!" -Role Founder -Position Fullstack_Developer
  ./scripts/auth.ps1 login -Email "john@example.com" -Password "Passw0rd!"
  ./scripts/auth.ps1 profile-get
  ./scripts/auth.ps1 profile-update [-FullName "New Name"] [-Email "new@example.com"] [-Password "NewPass!1"] [-Role Backend_Developer] [-Position QA_Engineer]
  ./scripts/auth.ps1 logout

Environment:
  AIHUB_API_BASE_URL  Override API base URL (default: https://aihubtasktracker-bwbz.onrender.com)
'@ | Write-Host
}

function Invoke-Api {
  param(
    [Parameter(Mandatory=$true)][ValidateSet('GET','POST','PUT')][string]$Method,
    [Parameter(Mandatory=$true)][string]$Path,
    [object]$Body,
    [string]$Token
  )
  $base = Get-BaseUrl
  $uri = "$base$Path"
  $headers = @{}
  if ($Token) { $headers['Authorization'] = "Bearer $Token" }

  if ($PSBoundParameters.ContainsKey('Body') -and $null -ne $Body) {
    $json = $Body | ConvertTo-Json -Depth 10
    return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -Body $json -ContentType 'application/json'
  } else {
    return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers
  }
}

try {
  switch ($Command) {
    'register' {
      if (-not $FullName -or -not $Email -or -not $Password -or -not $Role -or -not $Position) {
        throw 'Missing required parameters. See help.'
      }
      $body = @{ full_name=$FullName; email=$Email; password=$Password; role=$Role; position=$Position }
      $res = Invoke-Api -Method 'POST' -Path '/api/v1/auth/register' -Body $body
      Write-Json $res
    }
    'login' {
      if (-not $Email -or -not $Password) { throw 'Missing Email/Password. See help.' }
      $res = Invoke-Api -Method 'POST' -Path '/api/v1/auth/login' -Body @{ email=$Email; password=$Password }
      if ($res.token) { Save-Token $res.token }
      Write-Json $res
    }
    'profile-get' {
      $token = Load-Token
      $res = Invoke-Api -Method 'GET' -Path '/api/v1/users/profile' -Token $token
      Write-Json $res
    }
    'profile-update' {
      $token = Load-Token
      $body = @{}
      if ($FullName) { $body.full_name = $FullName }
      if ($Email)    { $body.email     = $Email }
      if ($Password) { $body.password  = $Password }
      if ($Role)     { $body.role      = $Role }
      if ($Position) { $body.position  = $Position }
      if ($body.Count -eq 0) { throw 'No fields to update. Provide at least one.' }
      $res = Invoke-Api -Method 'PUT' -Path '/api/v1/users/profile' -Body $body -Token $token
      Write-Json $res
    }
    'logout' {
      $token = Load-Token
      $res = Invoke-Api -Method 'POST' -Path '/api/v1/auth/logout' -Token $token
      Clear-Token
      Write-Json $res
    }
    'help' { Show-Help }
  }
}
catch {
  if ($_.Exception.Response) {
    try {
      $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
      $reader.BaseStream.Position = 0
      $reader.DiscardBufferedData()
      $msg = $reader.ReadToEnd()
      Write-Error $msg
    } catch { Write-Error $_ }
  } else {
    Write-Error $_
  }
  exit 1
}

