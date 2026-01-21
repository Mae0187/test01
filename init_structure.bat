@echo off
echo [VibeCoding] Building Modular Architecture...

:: 1. Create Directories
mkdir src
mkdir src\ui
mkdir src\logic
mkdir src\utils
mkdir bin

:: 2. Create __init__.py to make them packages
type nul > src\__init__.py
type nul > src\ui\__init__.py
type nul > src\logic\__init__.py
type nul > src\utils\__init__.py

:: 3. Create placeholder logic files (Empty for now)
type nul > src\logic\downloader.py
type nul > src\logic\core_manager.py

echo [SUCCESS] Directory structure created.
echo [INFO] You can now populate the python files.
pause