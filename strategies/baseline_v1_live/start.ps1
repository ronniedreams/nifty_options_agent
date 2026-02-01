# Baseline V1 Live Trading - Quick Start Script
# 
# This script helps you quickly launch the baseline_v1 strategy
# with sensible defaults.

param(
    [Parameter(Mandatory=$false)]
    [string]$Expiry = "",
    
    [Parameter(Mandatory=$false)]
    [int]$ATM = 0,
    
    [Parameter(Mandatory=$false)]
    [switch]$Paper = $false
)

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Baseline V1 Live Trading - Quick Start" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# Check if running from correct directory
if (-not (Test-Path ".\baseline_v1_live.py")) {
    Write-Host "ERROR: Please run this script from the 'live' directory" -ForegroundColor Red
    Write-Host "cd options_agent\live" -ForegroundColor Yellow
    exit 1
}

# Check for .env file
if (-not (Test-Path ".env")) {
    Write-Host "WARNING: .env file not found" -ForegroundColor Yellow
    Write-Host "Creating .env from template..." -ForegroundColor Yellow
    
    @"
# OpenAlgo Connection
OPENALGO_API_KEY=your_api_key_here
OPENALGO_HOST=http://127.0.0.1:5000
OPENALGO_WS_URL=ws://127.0.0.1:8765

# Trading Mode
PAPER_TRADING=true
DRY_RUN=false

# Optional
TELEGRAM_ENABLED=false
VERBOSE=false
"@ | Out-File -FilePath ".env" -Encoding utf8
    
    Write-Host "Created .env file. Please edit it with your API key." -ForegroundColor Green
    Write-Host ""
}

# Load .env
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') {
            $key = $matches[1].Trim()
            $value = $matches[2].Trim()
            [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
}

# Get expiry if not provided
if ([string]::IsNullOrEmpty($Expiry)) {
    Write-Host "Available expiries (NIFTY):" -ForegroundColor Yellow
    Write-Host "  - Current week:  e.g., 26DEC24" -ForegroundColor Gray
    Write-Host "  - Monthly:       e.g., 31DEC24" -ForegroundColor Gray
    Write-Host ""
    $Expiry = Read-Host "Enter expiry date (format: DDMMMYY)"
}

# Get ATM if not provided
if ($ATM -eq 0) {
    Write-Host ""
    Write-Host "Current NIFTY levels (approx):" -ForegroundColor Yellow
    Write-Host "  - Check live: https://www.nseindia.com/" -ForegroundColor Gray
    Write-Host "  - Round to nearest 50 (e.g., 18000, 18050)" -ForegroundColor Gray
    Write-Host ""
    $ATM = Read-Host "Enter ATM strike price"
}

# Override paper trading if switch provided
if ($Paper) {
    [System.Environment]::SetEnvironmentVariable("PAPER_TRADING", "true", "Process")
}

# Display configuration
Write-Host ""
Write-Host "Configuration:" -ForegroundColor Cyan
Write-Host "  Expiry:          $Expiry" -ForegroundColor White
Write-Host "  ATM Strike:      $ATM" -ForegroundColor White
Write-Host "  Paper Trading:   $env:PAPER_TRADING" -ForegroundColor White
Write-Host "  API Key:         $($env:OPENALGO_API_KEY.Substring(0, [Math]::Min(10, $env:OPENALGO_API_KEY.Length)))..." -ForegroundColor White
Write-Host ""

# Confirm
$confirm = Read-Host "Start trading? (y/n)"

if ($confirm -ne 'y') {
    Write-Host "Cancelled." -ForegroundColor Yellow
    exit 0
}

# Check if OpenAlgo is running
Write-Host ""
Write-Host "Checking OpenAlgo connection..." -ForegroundColor Cyan

try {
    $response = Invoke-WebRequest -Uri "$env:OPENALGO_HOST/api/v1/" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "✓ OpenAlgo is running" -ForegroundColor Green
} catch {
    Write-Host "✗ Cannot connect to OpenAlgo at $env:OPENALGO_HOST" -ForegroundColor Red
    Write-Host "  Please start OpenAlgo first:" -ForegroundColor Yellow
    Write-Host "  cd ../openalgo && python app.py" -ForegroundColor Gray
    exit 1
}

# Create logs directory
if (-not (Test-Path ".\logs")) {
    New-Item -ItemType Directory -Path ".\logs" | Out-Null
    Write-Host "Created logs directory" -ForegroundColor Green
}

# Start strategy
Write-Host ""
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Starting Baseline V1 Live Trading..." -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

python baseline_v1_live.py --expiry $Expiry --atm $ATM

# Cleanup on exit
Write-Host ""
Write-Host "Strategy stopped." -ForegroundColor Yellow
Write-Host "Check logs in ./logs/ directory" -ForegroundColor Cyan
