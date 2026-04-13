# View Weather Daemon Logs
# Usage: .\Logs.ps1 [-Lines 50] [-Follow]

param(
    [int]$Lines = 50,
    [switch]$Follow
)

$User = "root"
. "$PSScriptRoot\_Env.ps1"

Write-Host "=== Weather Daemon Logs ===" -ForegroundColor Green

if ($Follow) {
    Write-Host "Following logs (Ctrl+C to stop)..." -ForegroundColor Yellow
    ssh "$User@$ServerIP" "journalctl -u weather-daemon -f"
} else {
    Write-Host "Last $Lines log entries:" -ForegroundColor Yellow
    ssh "$User@$ServerIP" "journalctl -u weather-daemon -n $Lines --no-pager"
}
