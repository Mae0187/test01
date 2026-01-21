@echo off
echo [VibeCoding] Initializing Environment for yt-dlp GUI (Ultimate)...

:: 1. Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.10+.
    pause
    exit /b
)

:: 2. Create venv if not exists
if not exist "venv" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
)

:: 3. Activate and Install Dependencies
call venv\Scripts\activate

echo [INFO] Upgrading pip...
python -m pip install --upgrade pip

echo [INFO] Installing Core Dependencies (PySide6, yt-dlp)...
pip install PySide6 yt-dlp

echo [INFO] Installing Sniffer Dependencies (Selenium, WebDriver)...
:: [FIX] 安裝嗅探器必備套件
pip install selenium webdriver-manager

echo.
echo [SUCCESS] Environment is ready.
echo [INFO] Run 'python main.py' to test the UI.
pause