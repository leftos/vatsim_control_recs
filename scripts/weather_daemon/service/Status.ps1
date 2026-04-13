# Check Weather Daemon Status
# Usage: .\Status.ps1

$User = "root"
. "$PSScriptRoot\_Env.ps1"

Write-Host "=== Weather Daemon Status ===" -ForegroundColor Green

Write-Host "`nTimer Status:" -ForegroundColor Yellow
ssh "$User@$ServerIP" "systemctl status weather-daemon.timer --no-pager"

Write-Host "`nLast Service Run:" -ForegroundColor Yellow
ssh "$User@$ServerIP" "systemctl status weather-daemon --no-pager -l"
