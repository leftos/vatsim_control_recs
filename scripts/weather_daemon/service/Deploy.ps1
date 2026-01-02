# Deploy Weather Daemon to Server
# Usage: .\Deploy.ps1

$ErrorActionPreference = "Stop"

$Domain = "leftos.dev"
$User = "root"
$RemotePath = "/opt/vatsim-weather-daemon"
$ProjectRoot = (Get-Item "$PSScriptRoot\..\..\..").FullName

Write-Host "=== VATSIM Weather Daemon Deployment ===" -ForegroundColor Green

# Resolve IP
Write-Host "Resolving $Domain..." -ForegroundColor Yellow
$ServerIP = [System.Net.Dns]::GetHostAddresses($Domain) |
    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
    Select-Object -First 1 -ExpandProperty IPAddressToString

if (-not $ServerIP) {
    Write-Host "Error: Could not resolve $Domain" -ForegroundColor Red
    exit 1
}
Write-Host "Resolved to: $ServerIP" -ForegroundColor Cyan

# Files to deploy
$Files = @(
    "scripts/weather_daemon/__init__.py"
    "scripts/weather_daemon/cli.py"
    "scripts/weather_daemon/config.py"
    "scripts/weather_daemon/generator.py"
    "scripts/weather_daemon/index_generator.py"
    "scripts/weather_daemon/artcc_boundaries.py"
    "scripts/weather_daemon/service/weather-daemon.service"
    "scripts/weather_daemon/service/weather-daemon.timer"
    "backend/__init__.py"
    "backend/core/__init__.py"
    "backend/core/analysis.py"
    "backend/core/calculations.py"
    "backend/core/groupings.py"
    "backend/core/models.py"
    "backend/core/flights.py"
    "backend/core/controllers.py"
    "backend/data/__init__.py"
    "backend/data/loaders.py"
    "backend/data/weather.py"
    "backend/data/vatsim_api.py"
    "backend/data/atis_filter.py"
    "backend/cache/__init__.py"
    "backend/cache/manager.py"
    "backend/config/__init__.py"
    "backend/config/constants.py"
    "ui/__init__.py"
    "ui/config.py"
    "ui/modals/__init__.py"
    "ui/modals/metar_info.py"
    "airport_disambiguator/__init__.py"
    "airport_disambiguator/disambiguator.py"
    "airport_disambiguator/disambiguation_engine.py"
    "airport_disambiguator/entity_extractor.py"
    "airport_disambiguator/name_processor.py"
    "common.py"
    "data/APT_BASE.csv"
    "data/airports.json"
    "data/iata-icao.csv"
    "data/custom_groupings.json"
    "requirements.txt"
)

Write-Host "Creating directory structure on remote..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "mkdir -p $RemotePath/scripts/weather_daemon/service $RemotePath/backend/core $RemotePath/backend/data $RemotePath/backend/cache $RemotePath/backend/config $RemotePath/ui/modals $RemotePath/airport_disambiguator $RemotePath/data/preset_groupings"

Write-Host "Uploading files..." -ForegroundColor Yellow
foreach ($file in $Files) {
    $localFile = Join-Path $ProjectRoot $file
    if (Test-Path $localFile) {
        Write-Host "  $file" -ForegroundColor Cyan
        scp $localFile "${User}@${ServerIP}:$RemotePath/$file"
    } else {
        Write-Host "  Warning: $file not found" -ForegroundColor Yellow
    }
}

# Upload preset groupings
Write-Host "Uploading preset groupings..." -ForegroundColor Yellow
$presetDir = Join-Path $ProjectRoot "data/preset_groupings"
if (Test-Path $presetDir) {
    Get-ChildItem "$presetDir/*.json" | ForEach-Object {
        Write-Host "  preset_groupings/$($_.Name)" -ForegroundColor Cyan
        scp $_.FullName "${User}@${ServerIP}:$RemotePath/data/preset_groupings/"
    }
}

Write-Host "Updating systemd services..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cp $RemotePath/scripts/weather_daemon/service/weather-daemon.service /etc/systemd/system/ && cp $RemotePath/scripts/weather_daemon/service/weather-daemon.timer /etc/systemd/system/ && systemctl daemon-reload"

Write-Host "Installing Python dependencies..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && source .venv/bin/activate && pip install -r requirements.txt"

Write-Host "Running weather generation..." -ForegroundColor Yellow
ssh "$User@$ServerIP" "cd $RemotePath && sudo -u www-data .venv/bin/python -m scripts.weather_daemon.cli --output /var/www/leftos.dev/weather --verbose"

Write-Host ""
Write-Host "=== Deployment Complete! ===" -ForegroundColor Green
Write-Host "Weather briefings updated at https://leftos.dev/weather/" -ForegroundColor Cyan
