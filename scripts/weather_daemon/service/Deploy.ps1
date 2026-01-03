# Deploy Weather Daemon to Server
# Usage: .\Deploy.ps1

$ErrorActionPreference = "Stop"

$Domain = "leftos.dev"
$User = "root"
$RemotePath = "/opt/vatsim-weather-daemon"
$ProjectRoot = (Get-Item "$PSScriptRoot\..\..\..").FullName

Write-Host "=== VATSIM Weather Daemon Deployment ===" -ForegroundColor Green

# Resolve IP
Write-Host "Resolving $Domain..." -ForegroundColor Yellow
$ServerIP = [System.Net.Dns]::GetHostAddresses($Domain) |
    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
    Select-Object -First 1 -ExpandProperty IPAddressToString

if (-not $ServerIP) {
    Write-Host "Error: Could not resolve $Domain" -ForegroundColor Red
    exit 1
}
Write-Host "Resolved to: $ServerIP" -ForegroundColor Cyan

# Stop the timer and service before deployment
Write-Host "Stopping weather daemon timer and service..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "systemctl stop weather-daemon.timer 2>/dev/null || true; systemctl stop weather-daemon.service 2>/dev/null || true"
Write-Host "Services stopped" -ForegroundColor Cyan

# Directories to deploy (will sync all files recursively)
$Directories = @(
    "scripts/weather_daemon"
    "backend"
    "ui"
    "airport_disambiguator"
    "data"
)

# Root-level files to deploy
$RootFiles = @(
    "common.py"
    "requirements.txt"
)

Write-Host "Creating directory structure on remote..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "mkdir -p $RemotePath/scripts/weather_daemon/service $RemotePath/backend/core $RemotePath/backend/data $RemotePath/backend/cache $RemotePath/backend/config $RemotePath/ui/modals $RemotePath/airport_disambiguator $RemotePath/data/preset_groupings"

Write-Host "Syncing directories..." -ForegroundColor Yellow
foreach ($dir in $Directories) {
    $localDir = Join-Path $ProjectRoot $dir
    if (Test-Path $localDir) {
        Write-Host "  $dir/" -ForegroundColor Cyan
        # Use scp -r for recursive copy (rsync not available on Windows)
        scp -r "$localDir/*" "${User}@${ServerIP}:$RemotePath/$dir/"
    } else {
        Write-Host "  Warning: $dir not found" -ForegroundColor Yellow
    }
}

Write-Host "Uploading root files..." -ForegroundColor Yellow
foreach ($file in $RootFiles) {
    $localFile = Join-Path $ProjectRoot $file
    if (Test-Path $localFile) {
        Write-Host "  $file" -ForegroundColor Cyan
        scp $localFile "${User}@${ServerIP}:$RemotePath/$file"
    } else {
        Write-Host "  Warning: $file not found" -ForegroundColor Yellow
    }
}

Write-Host "Updating systemd services..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cp $RemotePath/scripts/weather_daemon/service/weather-daemon.service /etc/systemd/system/ && cp $RemotePath/scripts/weather_daemon/service/weather-daemon.timer /etc/systemd/system/ && systemctl daemon-reload"

Write-Host "Installing Python dependencies..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && source .venv/bin/activate && pip install -r requirements.txt"

Write-Host "Clearing caches (except weather)..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "rm -f $RemotePath/cache/artcc_boundaries/*.json 2>/dev/null; rm -rf $RemotePath/cache/simaware_boundaries/* 2>/dev/null; rm -f $RemotePath/cache/simaware_facilities*.json 2>/dev/null; echo 'Caches cleared'"

Write-Host "Running weather generation..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && sudo -u www-data .venv/bin/python -m scripts.weather_daemon.cli --output /var/www/leftos.dev/weather --verbose"

# Restart the timer (which will trigger the service on schedule)
Write-Host "Restarting weather daemon timer..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "systemctl enable weather-daemon.timer && systemctl start weather-daemon.timer"
Write-Host "Timer restarted" -ForegroundColor Cyan

Write-Host ""
Write-Host "=== Deployment Complete! ===" -ForegroundColor Green
Write-Host "Weather briefings updated at https://leftos.dev/weather/" -ForegroundColor Cyan
Write-Host "Timer active - next run in ~15 minutes" -ForegroundColor Cyan
