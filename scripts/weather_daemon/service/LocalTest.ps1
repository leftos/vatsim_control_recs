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
#
# The page is served at http://localhost:8765/index.html (the CARTO basemap key is
# locked to localhost and needs the Referer a file:// page does not send).
# Press Enter in this window to stop the server.

param(
    [string]$Stages = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Get-Item "$ScriptDir\..\..\..").FullName
$OutputDir = "$ProjectRoot\test_output"

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

    Write-Host ""
    Write-Host "Installing dependencies..." -ForegroundColor Yellow
    uv sync
    if ($LASTEXITCODE -ne 0) {
        Write-Host "uv sync failed with exit code $LASTEXITCODE" -ForegroundColor Red
        exit 1
    }

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

    uv run python @CliArgs

    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "=== Generation Complete! ===" -ForegroundColor Green
        Write-Host "Output: $OutputDir" -ForegroundColor Cyan

        # Serve over HTTP on localhost and open in browser
        $IndexPath = "$OutputDir\index.html"
        if (Test-Path $IndexPath) {
            $Port = 8765
            $Url = "http://localhost:$Port/index.html"
            Write-Host "Serving $OutputDir at $Url" -ForegroundColor Yellow
            $Server = Start-Process -FilePath "uv" -PassThru -WindowStyle Hidden -ArgumentList @(
                "run", "python", "-m", "http.server", "$Port",
                "--bind", "127.0.0.1", "--directory", "`"$OutputDir`""
            )
            try {
                # Wait up to ~10 s for the server to answer
                $Ready = $false
                for ($i = 0; $i -lt 20 -and -not $Server.HasExited; $i++) {
                    try {
                        Invoke-WebRequest -Uri "http://localhost:$Port/" -UseBasicParsing -TimeoutSec 1 | Out-Null
                        $Ready = $true
                        break
                    } catch {
                        Start-Sleep -Milliseconds 500
                    }
                }
                if (-not $Ready) {
                    $Reason = if ($Server.HasExited) { "exited with code $($Server.ExitCode)" } else { "did not answer within 10 s" }
                    Write-Host "Error: the HTTP server on port $Port $Reason (is the port in use?)" -ForegroundColor Red
                    exit 1
                }
                Start-Process $Url
                Read-Host "Press Enter to stop the server"
            } finally {
                if (-not $Server.HasExited) {
                    # /T also stops the python process uv started
                    taskkill /PID $Server.Id /T /F 2>&1 | Out-Null
                }
            }
        }
    } else {
        Write-Host "Generation failed with exit code $LASTEXITCODE" -ForegroundColor Red
        exit 1
    }
} finally {
    Pop-Location
}
