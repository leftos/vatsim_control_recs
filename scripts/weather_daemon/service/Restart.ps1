# Restart Weather Daemon Timer
# Usage: .\Restart.ps1

$User = "root"
. "$PSScriptRoot\_Env.ps1"

Write-Host "=== Restarting Weather Daemon Timer ===" -ForegroundColor Green

Write-Host "Restarting timer on $ServerIP..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "systemctl restart weather-daemon.timer"

Write-Host "Timer restarted." -ForegroundColor Green
ssh "$User@$ServerIP" "systemctl status weather-daemon.timer --no-pager"
