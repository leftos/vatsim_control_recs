# Regenerate Briefings Using Cached Weather
# Usage: .\RegenCached.ps1
#
# Stages: briefings, tiles, index (uses cached weather)
#
# Uses previously fetched weather data to regenerate all briefings.
# Much faster than full regeneration since it skips API calls.
# Useful for testing code changes to HTML generation.

$ErrorActionPreference = "Stop"

$User = "root"
$RemotePath = "/opt/vatsim-weather-daemon"
. "$PSScriptRoot\_Env.ps1"

Write-Host "=== Regenerating with Cached Weather ===" -ForegroundColor Green
Write-Host "Server: $ServerIP" -ForegroundColor Cyan

Write-Host "Pulling latest code..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && git reset --hard HEAD && git clean -fd && git pull"

Write-Host "Regenerating briefings (cached weather)..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && sudo -u www-data .venv/bin/python -m scripts.weather_daemon.cli --output /var/www/leftos.dev/weather --stages briefings,tiles,index --verbose"

Write-Host ""
Write-Host "=== Cached Regeneration Complete! ===" -ForegroundColor Green
Write-Host "View at https://leftos.dev/weather/" -ForegroundColor Cyan
