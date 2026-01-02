# View Weather Daemon Logs
# Usage: .\Logs.ps1 [-Lines 50] [-Follow]

param(
    [int]$Lines = 50,
    [switch]$Follow
)

$Domain = "leftos.dev"
$User = "root"

Write-Host "=== Weather Daemon Logs ===" -ForegroundColor Green

$ServerIP = [System.Net.Dns]::GetHostAddresses($Domain) |
    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
    Select-Object -First 1 -ExpandProperty IPAddressToString

if ($Follow) {
    Write-Host "Following logs (Ctrl+C to stop)..." -ForegroundColor Yellow
    ssh "$User@$ServerIP" "journalctl -u weather-daemon -f"
} else {
    Write-Host "Last $Lines log entries:" -ForegroundColor Yellow
    ssh "$User@$ServerIP" "journalctl -u weather-daemon -n $Lines --no-pager"
}
