@echo off
REM Launches the MTD Classifier GUI. Double-click this file to start the app.
setlocal
cd /d "%~dp0"

where mtd-gui >nul 2>nul
if errorlevel 1 (
    echo Installing mtd-classifier...
    python -m pip install --quiet -e .
    if errorlevel 1 (
        echo.
        echo Install failed. Make sure Python is installed and on PATH.
        pause
        exit /b 1
    )
)

start "" mtd-gui
