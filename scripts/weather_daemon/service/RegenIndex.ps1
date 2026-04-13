# Regenerate Index Only (no weather fetch)
# Usage: .\RegenIndex.ps1
#
# Stages: index only
#
# Quick way to update map/UI without re-fetching weather data.

$ErrorActionPreference = "Stop"

$User = "root"
$RemotePath = "/opt/vatsim-weather-daemon"
. "$PSScriptRoot\_Env.ps1"

Write-Host "=== Regenerating Index Page ===" -ForegroundColor Green
Write-Host "Server: $ServerIP" -ForegroundColor Cyan

Write-Host "Pulling latest code..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && git reset --hard HEAD && git clean -fd && git pull"

Write-Host "Regenerating index page..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && sudo -u www-data .venv/bin/python -m scripts.weather_daemon.cli --output /var/www/leftos.dev/weather --stages index --verbose"

Write-Host ""
Write-Host "=== Index Regeneration Complete! ===" -ForegroundColor Green
Write-Host "View at https://leftos.dev/weather/" -ForegroundColor Cyan
