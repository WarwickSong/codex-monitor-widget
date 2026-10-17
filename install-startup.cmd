@echo off
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Startup') + '\CodexQuotaWidget.lnk'); $lnk.TargetPath = '%~dp0start.cmd'; $lnk.WorkingDirectory = '%~dp0'; $lnk.Save()"
echo [OK] 已注册开机自启
pause
