@echo off
setlocal
set "APP_DIR=%~dp0"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP_DIR%\Codex Monitor Widget.lnk"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$shell = New-Object -ComObject WScript.Shell; $shortcut = $shell.CreateShortcut('%SHORTCUT%'); $shortcut.TargetPath = Join-Path '%APP_DIR%' 'start-monitor.cmd'; $shortcut.WorkingDirectory = '%APP_DIR%'; $shortcut.WindowStyle = 7; $shortcut.Description = 'Codex Monitor Widget'; $shortcut.Save()"

echo Installed startup shortcut:
echo %SHORTCUT%
pause
