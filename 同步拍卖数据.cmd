@echo off
where pwsh.exe >nul 2>nul
if %errorlevel%==0 (
  pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\sync-market.ps1" %*
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\sync-market.ps1" %*
)
set sync_exit=%errorlevel%
if "%~1"=="" pause
exit /b %sync_exit%
