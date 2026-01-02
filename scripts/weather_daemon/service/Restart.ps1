# Restart Weather Daemon Timer
# Usage: .\Restart.ps1

$Domain = "leftos.dev"
$User = "root"

Write-Host "=== Restarting Weather Daemon Timer ===" -ForegroundColor Green

$ServerIP = [System.Net.Dns]::GetHostAddresses($Domain) |
    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
    Select-Object -First 1 -ExpandProperty IPAddressToString

Write-Host "Restarting timer on $ServerIP..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "systemctl restart weather-daemon.timer"

Write-Host "Timer restarted." -ForegroundColor Green
ssh "$User@$ServerIP" "systemctl status weather-daemon.timer --no-pager"
