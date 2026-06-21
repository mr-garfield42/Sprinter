@echo off
REM Double-clickable launcher for the Sprinter math solver.
REM Works regardless of how .py files are associated on this PC.
cd /d "%~dp0"

REM Make sure dependencies are installed (quiet check).
py -c "import PIL, requests, pygetwindow" 2>nul
if errorlevel 1 (
    echo Installing required packages, please wait...
    py -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Could not install dependencies. Press any key to close.
        pause >nul
        exit /b 1
    )
)

REM Launch the GUI with no console window.
start "" pythonw "%~dp0math_solver.py"
