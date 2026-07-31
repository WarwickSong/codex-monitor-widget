@echo off
setlocal
cd /d "%~dp0"

set "PYTHONW="
set "PYTHONEXE="

REM 1) Try pythonw.exe directly in PATH
where pythonw.exe >nul 2>nul
if not errorlevel 1 (
    set "PYTHONW=pythonw.exe"
    goto :found
)

REM 2) Try python.exe in PATH, then look for pythonw.exe next to it
for /f "delims=" %%P in ('where python.exe 2^>nul') do (
    if not defined PYTHONW if exist "%%~dpPpythonw.exe" (
        set "PYTHONW=%%~dpPpythonw.exe"
        set "PYTHONEXE=%%P"
    )
)

REM 3) Check common install locations (Anaconda, Miniconda, python.org)
if not defined PYTHONW for %%P in (
    "%USERPROFILE%\anaconda3\pythonw.exe"
    "%USERPROFILE%\miniconda3\pythonw.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python313\pythonw.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python312\pythonw.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python311\pythonw.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python310\pythonw.exe"
    "C:\anaconda3\pythonw.exe"
    "C:\miniconda3\pythonw.exe"
    "C:\Program Files\Python313\pythonw.exe"
    "C:\Program Files\Python312\pythonw.exe"
    "C:\Program Files\Python311\pythonw.exe"
    "C:\Program Files\Python310\pythonw.exe"
) do (
    if not defined PYTHONW if exist "%%~P" set "PYTHONW=%%~P"
)

:found
if not defined PYTHONW (
    echo [Codex Monitor] Cannot find pythonw.exe.
    echo.
    echo Please install Python 3.10+ with PySide6, or add your Python
    echo installation directory to PATH.
    echo.
    pause
    exit /b 1
)

REM Delete old crash log so we can detect new crashes
if exist "%~dp0crash.log" del "%~dp0crash.log"

REM Launch silently (no console window)
start "Codex Monitor" "%PYTHONW%" "%~dp0main.py"

REM Give it 2 seconds to start, then check for crash log
timeout /t 2 /nobreak >nul
if exist "%~dp0crash.log" (
    echo [Codex Monitor] Failed to start! Error log:
    echo.
    type "%~dp0crash.log"
    echo.
    pause
)
