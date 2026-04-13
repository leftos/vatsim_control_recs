# Run Weather Daemon Now (trigger immediate generation)
# Usage: .\RunNow.ps1

$User = "root"
. "$PSScriptRoot\_Env.ps1"

Write-Host "=== Running Weather Generation ===" -ForegroundColor Green

Write-Host "Triggering weather generation on $ServerIP..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "systemctl start weather-daemon"

Write-Host "Done! Check status with .\Status.ps1" -ForegroundColor Green
