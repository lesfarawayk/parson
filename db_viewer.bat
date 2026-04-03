@echo off
:: Parson DB Viewer — standalone database browser
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Run install.bat first.
    pause
    exit /b 1
)
venv\Scripts\python.exe db_viewer.py
pause
