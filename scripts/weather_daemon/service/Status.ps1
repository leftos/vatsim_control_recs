# Check Weather Daemon Status
# Usage: .\Status.ps1

$Domain = "leftos.dev"
$User = "root"

Write-Host "=== Weather Daemon Status ===" -ForegroundColor Green

$ServerIP = [System.Net.Dns]::GetHostAddresses($Domain) |
    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
    Select-Object -First 1 -ExpandProperty IPAddressToString

Write-Host "`nTimer Status:" -ForegroundColor Yellow
ssh "$User@$ServerIP" "systemctl status weather-daemon.timer --no-pager"

Write-Host "`nLast Service Run:" -ForegroundColor Yellow
ssh "$User@$ServerIP" "systemctl status weather-daemon --no-pager -l"
