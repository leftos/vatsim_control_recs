# Run Weather Daemon Now (trigger immediate generation)
# Usage: .\RunNow.ps1

$Domain = "leftos.dev"
$User = "root"

Write-Host "=== Running Weather Generation ===" -ForegroundColor Green

$ServerIP = [System.Net.Dns]::GetHostAddresses($Domain) |
    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
    Select-Object -First 1 -ExpandProperty IPAddressToString

Write-Host "Triggering weather generation on $ServerIP..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "systemctl start weather-daemon"

Write-Host "Done! Check status with .\Status.ps1" -ForegroundColor Green
