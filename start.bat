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

REM start.bat console → 终端模式；start.bat api → API 服务模式；默认 → GUI 图形界面
if /i "%~1"=="api" (
    echo [INFO] Starting API server... 文档: http://127.0.0.1:8100/docs
    python -m uvicorn api:app --host 127.0.0.1 --port 8100
    pause
    exit /b 0
)

if /i "%~1"=="console" (
    echo [INFO] Starting in console mode...
    python bot.py
    echo.
    echo [INFO] Program exited. Press any key to close...
    pause >nul
    exit /b 0
)

echo [INFO] Starting GUI... (logs: bot_gui.log)
if exist "venv\Scripts\pythonw.exe" (
    start "" venv\Scripts\pythonw.exe bot_gui.py
) else (
    start "" pythonw bot_gui.py
)
