@echo off
setlocal
echo [VibeCoding] Building with User's Proven Reference...

:: 設定變數
set EXE_NAME=YtDlpDownloader
set ICON_NAME=01.ico

:: 1. 環境檢查
call venv\Scripts\activate

:: 2. 清理舊檔
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "%EXE_NAME%.spec" del "%EXE_NAME%.spec"
if exist "Release" rmdir /s /q "Release"

:: 3. 執行 PyInstaller (完全參考您的成功參數)
:: --icon: 設定檔案總管圖示
:: --add-data "01.ico;.": 把圖示塞入 EXE 讓程式讀取 (Taskbar 用)
echo [INFO] Compiling...
pyinstaller --noconfirm --onefile --windowed ^
 --name "%EXE_NAME%" ^
 --icon "%ICON_NAME%" ^
 --add-data "%ICON_NAME%;." ^
 main.py

if %errorlevel% neq 0 (
    echo [FATAL] Build failed.
    pause
    exit /b
)

:: 4. 整理 Release 資料夾
echo [INFO] Organizing Release...
mkdir Release
move "dist\%EXE_NAME%.exe" "Release\" >nul

:: 自動補建 bin 資料夾 (提醒使用者放工具)
mkdir "Release\bin"

echo.
echo [SUCCESS] Build Complete.
echo ------------------------------------------------
echo * Please put 'yt-dlp.exe' into the 'Release\bin' folder.
echo * Run 'Release\%EXE_NAME%.exe' to test.
echo ------------------------------------------------
pause