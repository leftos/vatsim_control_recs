# Quick Deploy - Git pull on server and regenerate
# Usage: .\QuickDeploy.ps1
#
# This is faster than full Deploy.ps1 when you've already pushed to git.
# It just pulls the latest code and regenerates.

$ErrorActionPreference = "Stop"

$Domain = "leftos.dev"
$User = "root"
$RemotePath = "/opt/vatsim-weather-daemon"

Write-Host "=== Quick Deploy (Git Pull) ===" -ForegroundColor Green

$ServerIP = [System.Net.Dns]::GetHostAddresses($Domain) |
    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
    Select-Object -First 1 -ExpandProperty IPAddressToString

Write-Host "Resolved to: $ServerIP" -ForegroundColor Cyan

Write-Host "Pulling latest code..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && git pull"

Write-Host "Running weather generation..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && sudo -u www-data .venv/bin/python -m scripts.weather_daemon.cli --output /var/www/leftos.dev/weather --verbose"

Write-Host ""
Write-Host "=== Quick Deploy Complete! ===" -ForegroundColor Green
Write-Host "Weather briefings updated at https://leftos.dev/weather/" -ForegroundColor Cyan
