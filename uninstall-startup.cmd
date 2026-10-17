@echo off
powershell -NoProfile -Command "Remove-Item ([Environment]::GetFolderPath('Startup') + '\CodexQuotaWidget.lnk') -ErrorAction SilentlyContinue"
echo [OK] 已移除开机自启
pause
