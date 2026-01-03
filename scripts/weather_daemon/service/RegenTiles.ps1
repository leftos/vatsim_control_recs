# Regenerate Tiles Only
# Usage: .\RegenTiles.ps1
#
# Stages: tiles only (uses cached weather)
#
# Regenerates weather overlay tiles using cached weather data.
# Useful for testing tile generation changes without refetching weather.

$ErrorActionPreference = "Stop"

$Domain = "leftos.dev"
$User = "root"
$RemotePath = "/opt/vatsim-weather-daemon"

Write-Host "=== Regenerating Tiles Only ===" -ForegroundColor Green

$ServerIP = [System.Net.Dns]::GetHostAddresses($Domain) |
    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
    Select-Object -First 1 -ExpandProperty IPAddressToString

Write-Host "Resolved to: $ServerIP" -ForegroundColor Cyan

Write-Host "Pulling latest code..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && git reset --hard HEAD && git pull"

Write-Host "Regenerating tiles..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && sudo -u www-data .venv/bin/python -m scripts.weather_daemon.cli --output /var/www/leftos.dev/weather --stages tiles --verbose"

Write-Host ""
Write-Host "=== Tiles Regeneration Complete! ===" -ForegroundColor Green
Write-Host "View at https://leftos.dev/weather/" -ForegroundColor Cyan
