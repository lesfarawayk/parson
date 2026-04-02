@echo off
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
    python main.py
) else (
    echo [ERROR] Virtual environment not found. Run install.bat first.
)
pause
