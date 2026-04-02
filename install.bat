@echo off
setlocal enabledelayedexpansion
echo === Parson Installer ===
echo.

:: Priority: py -3.12 > py -3.11 > py -3.10 > python
set PYTHON_CMD=

:: Try py launcher with specific version first (best on Windows with multiple Pythons)
where py >nul 2>&1
if not errorlevel 1 (
    py -3.12 --version >nul 2>&1 && set PYTHON_CMD=py -3.12
    if "!PYTHON_CMD!"=="" py -3.11 --version >nul 2>&1 && set PYTHON_CMD=py -3.11
    if "!PYTHON_CMD!"=="" py -3.10 --version >nul 2>&1 && set PYTHON_CMD=py -3.10
)

:: Fallback to plain python
if "%PYTHON_CMD%"=="" (
    where python >nul 2>&1 && set PYTHON_CMD=python
)

if "%PYTHON_CMD%"=="" (
    echo [ERROR] Python not found. Install Python 3.12 from python.org
    pause
    exit /b 1
)

echo Found: %PYTHON_CMD%
%PYTHON_CMD% --version

:: Verify version is 3.10-3.12
%PYTHON_CMD% -c "import sys; v=sys.version_info; exit(0 if v.major==3 and 10<=v.minor<=12 else 1)"
if errorlevel 1 (
    echo.
    echo [ERROR] Need Python 3.10, 3.11, or 3.12. PySide6 does not support 3.13+.
    echo.
    echo You have Python 3.14 installed. Either:
    echo   1. Uninstall Python 3.14 via "Add or remove programs"
    echo   2. Or install Python 3.12 alongside and re-run this script
    pause
    exit /b 1
)

echo.
echo Creating virtual environment...
%PYTHON_CMD% -m venv venv

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Failed to create virtual environment.
    echo Try manually: %PYTHON_CMD% -m venv venv
    pause
    exit /b 1
)

echo Installing dependencies...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

echo Installing Playwright browsers...
venv\Scripts\python.exe -m playwright install chromium

echo.
echo === Installation complete ===
echo Run: start.bat
pause
