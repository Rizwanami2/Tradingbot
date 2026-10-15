@echo off
REM ==============================================================================
REM Nimble Darwin Trading Bot - Windows 11 & AWS EC2 Installer
REM Configures Python virtual environment, dependencies, and MetaTrader 5 on Windows.
REM ==============================================================================
chcp 65001 >nul
setlocal enabledelayedexpansion

echo =====================================================================
echo   Nimble Darwin Trading Bot - Windows Installation
echo =====================================================================
echo.

REM 1. Verify Python Installation
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    where py >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Python was not found in PATH!
        echo.
        echo Please install Python 3.10 or 3.11 from https://www.python.org/downloads/
        echo IMPORTANT: Check the box "Add python.exe to PATH" during installation.
        echo.
        pause
        exit /b 1
    ) else (
        set "PY_CMD=py -3"
    )
) else (
    set "PY_CMD=python"
)

echo [1/6] Found Python:
%PY_CMD% --version
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to run %PY_CMD%. Please verify Python installation.
    pause
    exit /b 1
)

REM 2. Create Virtual Environment if not present
echo.
echo [2/6] Checking virtual environment (venv)...
if not exist "venv\Scripts\activate.bat" (
    echo [*] Creating virtual environment in .\venv ...
    %PY_CMD% -m venv venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [*] Virtual environment created successfully.
) else (
    echo [*] Virtual environment already exists.
)

REM 3. Activate Virtual Environment
echo.
echo [3/6] Activating virtual environment...
call venv\Scripts\activate.bat
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

REM 4. Upgrade pip & wheel
echo.
echo [4/6] Upgrading pip, setuptools, and wheel...
python -m pip install --upgrade pip setuptools wheel --quiet

REM 5. Install Project Dependencies
echo.
echo [5/6] Installing dependencies from requirements.txt...
if exist "requirements.txt" (
    pip install -r requirements.txt
    if %ERRORLEVEL% neq 0 (
        echo [WARNING] Some dependencies had warnings or issues during install.
    )
) else (
    echo [WARNING] requirements.txt not found! Skipping.
)

REM Install MetaTrader 5 (native Windows package)
echo.
echo [*] Installing native MetaTrader5 package for Windows...
pip install MetaTrader5
if %ERRORLEVEL% neq 0 (
    echo [WARNING] MetaTrader5 pip package installation failed.
    echo (If you are on 32-bit Python, note that MetaTrader5 requires 64-bit Python).
)

REM 6. Environment configuration check
echo.
echo [6/6] Checking configuration (.env)...
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo [*] Created .env from .env.example.
        echo [!] IMPORTANT: Please edit .env to configure your TELEGRAM_TOKEN and MT5 credentials!
    ) else (
        echo [WARNING] Neither .env nor .env.example found.
    )
) else (
    echo [*] .env file is present.
)

REM 7. Quick verification
echo.
echo =====================================================================
echo   Verification & Detection
echo =====================================================================
python -c "import sys; print(f'Python version: {sys.version}')"
python -c "import MetaTrader5 as mt5; print('MetaTrader5 package: OK (version ' + getattr(mt5, '__version__', 'ready') + ')') if mt5 else print('MetaTrader5: not available')" 2>nul
if %ERRORLEVEL% neq 0 (
    echo [!] MetaTrader5 package check: skipped or not available.
)

REM Detect MT5 terminal executable
set "MT5_FOUND="
if exist "%ProgramFiles%\MetaTrader 5\terminal64.exe" set "MT5_FOUND=%ProgramFiles%\MetaTrader 5\terminal64.exe"
if not defined MT5_FOUND (
    if exist "%LOCALAPPDATA%\Programs\MetaTrader 5\terminal64.exe" set "MT5_FOUND=%LOCALAPPDATA%\Programs\MetaTrader 5\terminal64.exe"
)
if not defined MT5_FOUND (
    if exist "C:\Program Files\MetaTrader 5\terminal64.exe" set "MT5_FOUND=C:\Program Files\MetaTrader 5\terminal64.exe"
)

if defined MT5_FOUND (
    echo [*] MetaTrader 5 Terminal detected at:
    echo     !MT5_FOUND!
) else (
    echo [i] MetaTrader 5 desktop terminal was not found in standard paths.
    echo     If using MT5 broker, install MetaTrader 5 or set MT5_PATH in .env.
)

echo.
echo =====================================================================
echo   Installation Completed Successfully!
echo =====================================================================
echo.
echo To start the bot on Windows:
echo   Run:  start_windows.bat
echo   Or:   powershell -ExecutionPolicy Bypass -File start_windows.ps1
echo.
pause
