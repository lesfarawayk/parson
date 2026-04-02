@echo off
echo === Parson Installer ===
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

echo Creating virtual environment...
python -m venv venv
call venv\Scripts\activate.bat

echo Installing dependencies...
pip install -r requirements.txt

echo Installing Playwright browsers...
playwright install chromium

echo.
echo === Installation complete ===
echo Run: start.bat
pause
