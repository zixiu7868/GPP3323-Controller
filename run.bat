@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python launcher "py" was not found.
    echo Install Python 3.11 or newer from https://www.python.org/
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 goto :failed

    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    if errorlevel 1 goto :failed
    pip install -r requirements.txt
    if errorlevel 1 goto :failed
) else (
    call ".venv\Scripts\activate.bat"
)

python app.py
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo [ERROR] GPP-3323 Controller failed to start.
pause
exit /b 1
