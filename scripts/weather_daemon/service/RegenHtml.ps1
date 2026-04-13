# Regenerate HTML Pages Only (No Tiles)
# Usage: .\RegenHtml.ps1
#
# Stages: briefings, index (uses cached weather, skips tiles)
#
# Uses cached weather data to regenerate all HTML briefings and index.
# Skips tile generation for faster updates when only HTML/CSS changes.
# Useful for testing styling changes without waiting for tile generation.

$ErrorActionPreference = "Stop"

$User = "root"
$RemotePath = "/opt/vatsim-weather-daemon"
. "$PSScriptRoot\_Env.ps1"

Write-Host "=== Regenerating HTML Pages Only ===" -ForegroundColor Green
Write-Host "Server: $ServerIP" -ForegroundColor Cyan

# Stop the timer and service before deployment
Write-Host "Stopping weather daemon timer and service..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "systemctl stop weather-daemon.timer 2>/dev/null || true; systemctl stop weather-daemon.service 2>/dev/null || true"
Write-Host "Services stopped" -ForegroundColor Cyan

Write-Host "Pulling latest code..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && git reset --hard HEAD && git clean -fd && git pull"

Write-Host "Regenerating HTML pages (no tiles)..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && sudo -u www-data .venv/bin/python -m scripts.weather_daemon.cli --output /var/www/leftos.dev/weather --stages briefings,index --verbose"

# Restart the timer
Write-Host "Restarting weather daemon timer..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "systemctl enable weather-daemon.timer && systemctl start weather-daemon.timer"
Write-Host "Timer restarted" -ForegroundColor Cyan

Write-Host ""
Write-Host "=== HTML Regeneration Complete! ===" -ForegroundColor Green
Write-Host "View at https://leftos.dev/weather/" -ForegroundColor Cyan
Write-Host "Timer active - next full run in ~15 minutes" -ForegroundColor Cyan
