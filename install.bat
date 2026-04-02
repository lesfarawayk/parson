@echo off
echo === Parson Installer ===
echo.

:: Try to find Python: python, python3, or py launcher
set PYTHON_CMD=
where python >nul 2>&1 && set PYTHON_CMD=python
if "%PYTHON_CMD%"=="" where python3 >nul 2>&1 && set PYTHON_CMD=python3
if "%PYTHON_CMD%"=="" where py >nul 2>&1 && set PYTHON_CMD=py

if "%PYTHON_CMD%"=="" (
    echo [ERROR] Python not found in PATH.
    pause
    exit /b 1
)

echo Found Python: %PYTHON_CMD%
%PYTHON_CMD% --version

:: Check Python version (need 3.10-3.12, PySide6 does NOT support 3.13+)
%PYTHON_CMD% -c "import sys; v=sys.version_info; exit(0 if 10<=v.minor<=12 and v.major==3 else 1)"
if errorlevel 1 (
    echo.
    echo [ERROR] PySide6 requires Python 3.10, 3.11, or 3.12.
    echo Your Python is too new or too old.
    echo Download Python 3.12 from: https://www.python.org/downloads/release/python-3129/
    echo Make sure to check "Add Python to PATH" during install.
    echo.
    echo If you have multiple Python versions, use the py launcher:
    echo   py -3.12 -m venv venv
    pause
    exit /b 1
)

echo.
echo Creating virtual environment...
%PYTHON_CMD% -m venv venv

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
)

echo Installing dependencies...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt

echo Installing Playwright browsers...
venv\Scripts\python.exe -m playwright install chromium

echo.
echo === Installation complete ===
echo Run: start.bat
pause
