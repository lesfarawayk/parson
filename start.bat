@echo off
:: Switch to the directory where this script is located
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Run install.bat first.
    pause
    exit /b 1
)
venv\Scripts\python.exe main.py
pause
