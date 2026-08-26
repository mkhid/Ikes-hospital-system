@echo off
REM ===========================================================================
REM  Intelligent Hospital Workforce Scheduling System
REM
REM  Double-click this file to start the portal. It sets everything up the
REM  first time and starts straight away on every run after that.
REM ===========================================================================
setlocal
cd /d "%~dp0"

echo.
echo  ==========================================================
echo   Intelligent Hospital Workforce Scheduling System
echo  ==========================================================
echo.

REM --- Check Python is available -------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo  [X] Python is not installed, or not on your PATH.
    echo.
    echo      Install Python 3.11 or newer from https://www.python.org/downloads/
    echo      IMPORTANT: tick "Add python.exe to PATH" during installation,
    echo      then close this window and run start.bat again.
    echo.
    pause
    exit /b 1
)

REM --- Create the virtual environment --------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo  [1/4] Creating a private Python environment...
    python -m venv .venv
    if errorlevel 1 goto fail
) else (
    echo  [1/4] Python environment found.
)

REM --- Install dependencies -------------------------------------------------
echo  [2/4] Checking dependencies...
if exist "wheels\" (
    .venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check --no-index --find-links wheels -r requirements.txt >nul 2>&1
    if errorlevel 1 (
        echo        Offline bundle does not match this Python version.
        echo        Downloading from the internet instead...
        .venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r requirements.txt
        if errorlevel 1 goto failnet
    )
) else (
    .venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto failnet
)

REM --- Build the demonstration database ------------------------------------
if not exist "instance\hospital.db" (
    echo  [3/4] Building the demonstration hospital ^(about 15 seconds^)...
    .venv\Scripts\python.exe seed.py
    if errorlevel 1 goto fail
) else (
    echo  [3/4] Database found, keeping the existing data.
)

REM --- Run ------------------------------------------------------------------
echo  [4/4] Starting the portal...
echo.
echo  ----------------------------------------------------------
echo    Open your browser at:  http://127.0.0.1:5000
echo.
echo    Sign in with:          TH-ADM-001
echo    Password:              Password123
echo.
echo    Press Ctrl+C in this window to stop the portal.
echo  ----------------------------------------------------------
echo.

start "" http://127.0.0.1:5000
.venv\Scripts\python.exe run.py
goto end

:failnet
echo.
echo  [X] Could not install the dependencies.
echo      This step needs an internet connection the first time.
echo      Connect to the internet and run start.bat again.
echo.
pause
exit /b 1

:fail
echo.
echo  [X] Setup failed. Read the messages above for the reason.
echo.
pause
exit /b 1

:end
endlocal
