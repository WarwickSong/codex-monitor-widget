@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   Codex Monitor Widget - 一键安装
echo ============================================
echo.

REM ── 1. 查找 Python ──────────────────────────
set "PYTHONEXE="
set "PYTHONW="

where python.exe >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%P in ('where python.exe') do (
        if not defined PYTHONEXE set "PYTHONEXE=%%P"
    )
)

if not defined PYTHONEXE (
    for %%P in (
        "%USERPROFILE%\AppData\Local\Programs\Python\Python313\python.exe"
        "%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe"
        "%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe"
        "%USERPROFILE%\AppData\Local\Programs\Python\Python310\python.exe"
        "C:\Program Files\Python313\python.exe"
        "C:\Program Files\Python312\python.exe"
        "C:\Program Files\Python311\python.exe"
        "C:\Program Files\Python310\python.exe"
    ) do (
        if not defined PYTHONEXE if exist "%%~P" set "PYTHONEXE=%%~P"
    )
)

if not defined PYTHONEXE (
    echo [错误] 找不到 Python。请先安装 Python 3.10+。
    echo        下载地址: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

for %%I in ("%PYTHONEXE%") do set "PYDIR=%%~dpI"
if exist "%PYDIR%pythonw.exe" (
    set "PYTHONW=%PYDIR%pythonw.exe"
) else (
    set "PYTHONW=%PYTHONEXE%"
)

echo [1/3] Python: %PYTHONEXE%
echo.

REM ── 2. 安装依赖 ────────────────────────────
echo [2/3] 安装依赖 (PySide6, requests)...
"%PYTHONEXE%" -m pip install -r "%~dp0requirements.txt" --quiet
if errorlevel 1 (
    echo [警告] 依赖安装失败，请手动运行: pip install -r requirements.txt
) else (
    echo       依赖已就绪。
)
echo.

REM ── 3. 安装开机自启（可选）──────────────────
set /p AUTOSTART="是否设置开机自启? (y/N): "
if /i "!AUTOSTART!"=="y" (
    call "%~dp0install-startup.cmd" < nul
    echo 开机自启已安装。
) else (
    echo 跳过开机自启。
)
echo.

REM ── 4. 启动（首次启动会自动安装拦截触发器）────
echo [3/3] 启动 Codex Monitor Widget...
start "Codex Monitor" "%PYTHONW%" "%~dp0main.py"
timeout /t 2 /nobreak >nul
if exist "%~dp0crash.log" (
    echo [错误] 启动失败，错误日志:
    echo.
    type "%~dp0crash.log"
    echo.
    pause
) else (
    echo 启动成功！悬浮窗已出现在桌面右上角。
    echo.
    echo 提示: 双击 start-monitor.cmd 可再次启动。
    echo       双击 uninstall-startup.cmd 可移除开机自启。
    timeout /t 3 /nobreak >nul
)
