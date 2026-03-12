@echo off
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found
    pause
    exit /b 1
)
start "" pythonw "%~dp0claude_code_monitor.py"
