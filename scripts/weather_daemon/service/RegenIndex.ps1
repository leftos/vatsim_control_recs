# Regenerate Index Only (no weather fetch)
# Usage: .\RegenIndex.ps1
#
# Quick way to update map/UI without re-fetching weather data.

$ErrorActionPreference = "Stop"

$Domain = "leftos.dev"
$User = "root"
$RemotePath = "/opt/vatsim-weather-daemon"

Write-Host "=== Regenerating Index Page ===" -ForegroundColor Green

$ServerIP = [System.Net.Dns]::GetHostAddresses($Domain) |
    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
    Select-Object -First 1 -ExpandProperty IPAddressToString

Write-Host "Resolved to: $ServerIP" -ForegroundColor Cyan

Write-Host "Pulling latest code..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && git reset --hard HEAD && git pull"

Write-Host "Regenerating index page..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && sudo -u www-data .venv/bin/python -m scripts.weather_daemon.cli --output /var/www/leftos.dev/weather --index-only --verbose"

Write-Host ""
Write-Host "=== Index Regeneration Complete! ===" -ForegroundColor Green
Write-Host "View at https://leftos.dev/weather/" -ForegroundColor Cyan
