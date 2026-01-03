# Regenerate Briefings Using Cached Weather
# Usage: .\RegenCached.ps1
#
# Stages: briefings, tiles, index (uses cached weather)
#
# Uses previously fetched weather data to regenerate all briefings.
# Much faster than full regeneration since it skips API calls.
# Useful for testing code changes to HTML generation.

$ErrorActionPreference = "Stop"

$Domain = "leftos.dev"
$User = "root"
$RemotePath = "/opt/vatsim-weather-daemon"

Write-Host "=== Regenerating with Cached Weather ===" -ForegroundColor Green

$ServerIP = [System.Net.Dns]::GetHostAddresses($Domain) |
    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
    Select-Object -First 1 -ExpandProperty IPAddressToString

Write-Host "Resolved to: $ServerIP" -ForegroundColor Cyan

Write-Host "Pulling latest code..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && git reset --hard HEAD && git clean -fd && git pull"

Write-Host "Regenerating briefings (cached weather)..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && sudo -u www-data .venv/bin/python -m scripts.weather_daemon.cli --output /var/www/leftos.dev/weather --stages briefings,tiles,index --verbose"

Write-Host ""
Write-Host "=== Cached Regeneration Complete! ===" -ForegroundColor Green
Write-Host "View at https://leftos.dev/weather/" -ForegroundColor Cyan
