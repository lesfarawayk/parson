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
    echo Try one of these fixes:
    echo   1. Reinstall Python and check "Add Python to PATH"
    echo   2. Or run manually: py -m venv venv
    pause
    exit /b 1
)

echo Found Python: %PYTHON_CMD%
%PYTHON_CMD% --version

echo.
echo Creating virtual environment...
%PYTHON_CMD% -m venv venv
call venv\Scripts\activate.bat

echo Installing dependencies...
pip install -r requirements.txt

echo Installing Playwright browsers...
playwright install chromium

echo.
echo === Installation complete ===
echo Run: start.bat
pause
