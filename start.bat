@echo off
chcp 65001 >nul

cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.11+ from python.org
    echo [TIP] During installation, check "Add python.exe to PATH"
    pause
    exit /b 1
)

if not exist "venv" (
    echo [INFO] First run - creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate
    pip install -r requirements.txt
    echo [OK] Dependencies installed.
    echo.
) else (
    call venv\Scripts\activate
)

if not exist "config.json" (
    echo [ERROR] config.json not found!
    echo [INFO] Please copy config.example.json to config.json
    echo [INFO] Then fill in your Bilibili cookies and DeepSeek API key.
    pause
    exit /b 1
)

echo [INFO] Starting Bilibili AI Agent...
echo.
python bot.py

echo.
echo [INFO] Program exited. Press any key to close...
pause >nul
