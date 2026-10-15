@echo off
REM ==============================================================================
REM Nimble Darwin Trading Bot - Windows 11 & AWS EC2 Launcher
REM Handles UTF-8 console output, virtual environment activation, and auto-restart.
REM ==============================================================================
chcp 65001 >nul
setlocal enabledelayedexpansion

REM Ensure Python uses UTF-8 encoding for standard I/O and file system operations
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

title Nimble Darwin Trading Bot - AWS Windows 11

echo =====================================================================
echo   Nimble Darwin Trading Bot - Windows Supervisor
echo =====================================================================
echo.

REM 1. Locate Virtual Environment
if exist "venv\Scripts\activate.bat" (
    echo [*] Activating virtual environment (venv)...
    call venv\Scripts\activate.bat
    set "PY_EXEC=python"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set "PY_EXEC=python"
    ) else (
        where py >nul 2>&1
        if %ERRORLEVEL% equ 0 (
            set "PY_EXEC=py -3"
        ) else (
            echo [ERROR] Neither virtual environment nor Python was found.
            echo Please run install_windows.bat first.
            echo.
            pause
            exit /b 1
        )
    )
)

REM 2. Verify .env file
if not exist ".env" (
    echo [WARNING] No .env file detected in current directory!
    if exist ".env.example" (
        echo [*] Copying .env.example to .env ...
        copy ".env.example" ".env" >nul
        echo [!] Created .env. Please configure TELEGRAM_TOKEN and credentials before trading.
    )
)

REM 3. Run Supervisor Loop
set "RESTART_COUNT=0"
set "MAX_RESTARTS=100"

:RUN_LOOP
echo.
echo [%DATE% %TIME%] Starting Nimble Darwin Trading Bot (run_bot.py)...
echo =====================================================================

%PY_EXEC% run_bot.py

set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo [!] Bot exited with code: %EXIT_CODE%

if %EXIT_CODE% equ 0 (
    echo [*] Clean shutdown requested. Exiting supervisor.
    goto END
)

set /a RESTART_COUNT+=1
if %RESTART_COUNT% geq %MAX_RESTARTS% (
    echo [ERROR] Maximum restart count (%MAX_RESTARTS%) reached. Halting supervisor.
    goto HALT
)

echo [i] Supervisor will auto-restart in 5 seconds (Attempt %RESTART_COUNT%/%MAX_RESTARTS%)...
echo     Press Ctrl+C to stop.
timeout /t 5 /nobreak >nul
goto RUN_LOOP

:HALT
echo.
echo [FATAL] Trading bot encountered repeated crashes. Check logs above.
pause

:END
