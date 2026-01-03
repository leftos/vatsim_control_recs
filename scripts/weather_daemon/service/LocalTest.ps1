# Local Test - Generate weather briefings locally and open in browser
# Usage: .\LocalTest.ps1
#
# Generates weather briefings to a local test_output folder and opens the result.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Get-Item "$ScriptDir\..\..\..").FullName
$OutputDir = "$ProjectRoot\test_output"

Write-Host "=== Local Weather Briefing Test ===" -ForegroundColor Green
Write-Host "Project root: $ProjectRoot" -ForegroundColor Cyan
Write-Host "Output dir: $OutputDir" -ForegroundColor Cyan

# Change to project root
Push-Location $ProjectRoot

try {
    Write-Host ""
    Write-Host "Generating weather briefings..." -ForegroundColor Yellow
    python -m scripts.weather_daemon.cli --output "$OutputDir" --verbose

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
