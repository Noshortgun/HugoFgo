@echo off
setlocal
"%SystemRoot%\System32\tasklist.exe" /FI "IMAGENAME eq hugofgo.exe" /NH | "%SystemRoot%\System32\find.exe" /I "hugofgo.exe" >nul
if not errorlevel 1 (
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "(New-Object -ComObject WScript.Shell).AppActivate('HugoFgo')" >nul 2>&1
    exit /b 0
)
cd /d "%~dp0.."
call npm run tauri dev
