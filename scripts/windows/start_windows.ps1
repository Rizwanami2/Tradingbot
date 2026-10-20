<#
.SYNOPSIS
    Nimble Darwin Trading Bot - Windows 11 & AWS EC2 PowerShell Launcher

.DESCRIPTION
    Runs the Nimble Darwin Trading Bot on Windows 11 / Windows Server on AWS EC2
    with full UTF-8 terminal encoding, virtual environment activation, and an
    auto-restart supervisor loop.

.PARAMETER Script
    Entry point script to execute (default: "run_bot.py").
    Options: "run_bot.py", "main.py", "main_full.py".

.PARAMETER AutoRestart
    Whether the supervisor should automatically restart the bot on unexpected crashes.
    Default: $true.

.PARAMETER MaxRestarts
    Maximum number of restart attempts before halting.
    Default: 100.

.EXAMPLE
    .\start_windows.ps1
    .\start_windows.ps1 -Script "main.py"
    .\start_windows.ps1 -AutoRestart:$false
#>

param(
    [string]$Script = "run_bot.py",
    [switch]$AutoRestart = $true,
    [int]$MaxRestarts = 100
)

# 1. Force UTF-8 Encoding for PowerShell session
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
$OutputEncoding           = [System.Text.Encoding]::UTF8

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

try {
    $host.ui.RawUI.WindowTitle = "Nimble Darwin Trading Bot - Windows 11 / AWS"
} catch {}

Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "   Nimble Darwin Trading Bot - Windows 11 / AWS Supervisor           " -ForegroundColor Cyan
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host ""

# 2. Locate Virtual Environment Python
$VenvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
$GlobalPython = (Get-Command python -ErrorAction SilentlyContinue).Source

if (Test-Path $VenvPython) {
    Write-Host "[*] Using virtual environment Python: $VenvPython" -ForegroundColor Green
    $PyCmd = $VenvPython
} elseif ($GlobalPython) {
    Write-Host "[*] Using system Python: $GlobalPython" -ForegroundColor Yellow
    $PyCmd = $GlobalPython
} else {
    Write-Host "[ERROR] Python executable not found!" -ForegroundColor Red
    Write-Host "Please run .\install_windows.bat first to configure the environment." -ForegroundColor Red
    Exit 1
}

# 3. Check .env configuration
$EnvFile = Join-Path $PSScriptRoot ".env"
$EnvExample = Join-Path $PSScriptRoot ".env.example"
if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile
        Write-Host "[!] Created .env from .env.example. Please review your credentials." -ForegroundColor Yellow
    } else {
        Write-Host "[WARNING] No .env file found in repository root." -ForegroundColor DarkYellow
    }
}

# 4. Supervisor Loop
$RestartCount = 0
$TargetScript = Join-Path $PSScriptRoot $Script

if (-not (Test-Path $TargetScript)) {
    Write-Host "[ERROR] Target script '$Script' not found in $PSScriptRoot!" -ForegroundColor Red
    Exit 1
}

do {
    $TimeStamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$TimeStamp] Launching $Script (Attempt $( $RestartCount + 1 ))..." -ForegroundColor Cyan
    Write-Host "---------------------------------------------------------------------" -ForegroundColor DarkGray

    & $PyCmd $TargetScript
    $ExitCode = $LASTEXITCODE

    Write-Host ""
    Write-Host "[!] Bot process exited with code: $ExitCode" -ForegroundColor Yellow

    if ($ExitCode -eq 0) {
        Write-Host "[*] Clean termination requested. Exiting supervisor." -ForegroundColor Green
        break
    }

    if (-not $AutoRestart) {
        Write-Host "[*] AutoRestart is disabled. Exiting." -ForegroundColor Yellow
        break
    }

    $RestartCount++
    if ($RestartCount -ge $MaxRestarts) {
        Write-Host "[ERROR] Reached maximum restart threshold ($MaxRestarts). Halting." -ForegroundColor Red
        break
    }

    Write-Host "[i] Auto-recovering in 5 seconds (Attempt $RestartCount of $MaxRestarts)..." -ForegroundColor Yellow
    Write-Host "    Press Ctrl+C to cancel." -ForegroundColor DarkGray
    Start-Sleep -Seconds 5

} while ($true)
