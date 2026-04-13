# Shared env loader for weather daemon service scripts.
# Dot-source this from other .ps1 scripts in this directory:
#     . "$PSScriptRoot\_Env.ps1"
#
# Reads WEATHER_SERVER_IP from the repo-root .env file (untracked) and
# exposes it as $ServerIP in the caller's scope.

$EnvPath = Join-Path $PSScriptRoot "..\..\..\.env"
if (-not (Test-Path $EnvPath)) {
    Write-Host "Error: .env file not found at $EnvPath" -ForegroundColor Red
    Write-Host "Create it with: WEATHER_SERVER_IP=<server-ip>" -ForegroundColor Yellow
    exit 1
}

$ServerIP = $null
foreach ($line in (Get-Content $EnvPath)) {
    if ($line -match '^\s*WEATHER_SERVER_IP\s*=\s*(.+?)\s*$') {
        $ServerIP = $matches[1].Trim('"').Trim("'")
        break
    }
}

if (-not $ServerIP) {
    Write-Host "Error: WEATHER_SERVER_IP not set in $EnvPath" -ForegroundColor Red
    exit 1
}
