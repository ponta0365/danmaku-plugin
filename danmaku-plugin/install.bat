@echo off
:: Check Administrator Privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [INFO] Requesting Administrator Privileges...
    powershell -Command "Start-Process cmd -ArgumentList '/c %~s0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
echo ==============================================
echo OBS NicoNico Danmaku Plugin Build and Installer
echo ==============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "admin_build_install.ps1"

echo.
pause
