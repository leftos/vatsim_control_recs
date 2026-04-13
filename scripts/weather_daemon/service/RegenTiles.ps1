# Regenerate Tiles Only
# Usage: .\RegenTiles.ps1
#
# Stages: tiles only (uses cached weather)
#
# Regenerates weather overlay tiles using cached weather data.
# Useful for testing tile generation changes without refetching weather.

$ErrorActionPreference = "Stop"

$User = "root"
$RemotePath = "/opt/vatsim-weather-daemon"
. "$PSScriptRoot\_Env.ps1"

Write-Host "=== Regenerating Tiles Only ===" -ForegroundColor Green
Write-Host "Server: $ServerIP" -ForegroundColor Cyan

Write-Host "Pulling latest code..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && git reset --hard HEAD && git clean -fd && git pull"

Write-Host "Regenerating tiles..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && sudo -u www-data .venv/bin/python -m scripts.weather_daemon.cli --output /var/www/leftos.dev/weather --stages tiles --verbose"

Write-Host ""
Write-Host "=== Tiles Regeneration Complete! ===" -ForegroundColor Green
Write-Host "View at https://leftos.dev/weather/" -ForegroundColor Cyan
