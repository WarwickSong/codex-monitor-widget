@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [!] 未找到 Python，请先安装 Python 3.10+
  pause & exit /b 1
)
if not exist ".venv" python -m venv .venv
call ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo [OK] 依赖安装完成
call install-startup.cmd
