# Local Test - Generate weather briefings locally and open in browser
# Usage: .\LocalTest.ps1 [stages]
#
# Stages (comma-separated): weather, briefings, tiles, index
# Default: all stages
#
# Examples:
#   .\LocalTest.ps1                    # Full generation
#   .\LocalTest.ps1 tiles,index        # Tiles and index only (use cached weather)
#   .\LocalTest.ps1 tiles              # Tiles only
#   .\LocalTest.ps1 index              # Index only

param(
    [string]$Stages = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Get-Item "$ScriptDir\..\..\..").FullName
$OutputDir = "$ProjectRoot\test_output"
$VenvPath = "$ProjectRoot\.venv"
$VenvPython = "$VenvPath\Scripts\python.exe"

Write-Host "=== Local Weather Briefing Test ===" -ForegroundColor Green
Write-Host "Project root: $ProjectRoot" -ForegroundColor Cyan
Write-Host "Output dir: $OutputDir" -ForegroundColor Cyan
if ($Stages) {
    Write-Host "Stages: $Stages" -ForegroundColor Cyan
} else {
    Write-Host "Stages: all (weather, briefings, tiles, index)" -ForegroundColor Cyan
}

# Change to project root
Push-Location $ProjectRoot

try {
    # Clear caches (except weather) to ensure fresh data
    Write-Host ""
    Write-Host "Clearing caches..." -ForegroundColor Yellow
    $CacheDir = "$ProjectRoot\cache"
    if (Test-Path "$CacheDir\artcc_boundaries") {
        Remove-Item "$CacheDir\artcc_boundaries\*" -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  Cleared artcc_boundaries cache" -ForegroundColor Cyan
    }
    if (Test-Path "$CacheDir\simaware_boundaries") {
        Remove-Item "$CacheDir\simaware_boundaries\*" -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  Cleared simaware_boundaries cache" -ForegroundColor Cyan
    }
    Get-ChildItem "$CacheDir\simaware_facilities*.json" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
    Write-Host "  Cleared simaware_facilities cache" -ForegroundColor Cyan

    # Check if venv exists, create if not
    if (-not (Test-Path $VenvPython)) {
        Write-Host ""
        Write-Host "Creating virtual environment..." -ForegroundColor Yellow
        python -m venv $VenvPath
    }

    # Install/update requirements
    Write-Host ""
    Write-Host "Installing dependencies..." -ForegroundColor Yellow
    & $VenvPython -m pip install -q -r requirements.txt

    Write-Host ""
    Write-Host "Generating weather briefings..." -ForegroundColor Yellow

    # Build command arguments
    $CliArgs = @(
        "-m", "scripts.weather_daemon.cli",
        "--output", "$OutputDir",
        "--verbose",
        "--workers", "20",
        "--tile-workers", "20"
    )

    if ($Stages) {
        $CliArgs += @("--stages", $Stages)
    }

    & $VenvPython @CliArgs

    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "=== Generation Complete! ===" -ForegroundColor Green
        Write-Host "Output: $OutputDir" -ForegroundColor Cyan

        # Open in browser
        $IndexPath = "$OutputDir\index.html"
        if (Test-Path $IndexPath) {
            Write-Host "Opening in browser..." -ForegroundColor Yellow
            Start-Process $IndexPath
        }
    } else {
        Write-Host "Generation failed with exit code $LASTEXITCODE" -ForegroundColor Red
        exit 1
    }
} finally {
    Pop-Location
}
