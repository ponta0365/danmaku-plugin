@echo off
title OBS Danmaku OAuth Bridge Launcher
echo ==============================================
echo OBS Danmaku OAuth Bridge Startup
echo ==============================================

set PYTHON_EXE="C:\Users\keymi\AppData\Local\Programs\Python\Python312\python.exe"

if not exist %PYTHON_EXE% (
    echo [ERROR] Python 3.12 was not found at %PYTHON_EXE%
    echo Please adjust the path in run.bat if Python 3.12 is located elsewhere.
    pause
    exit /b 1
)

echo [INFO] Verifying and installing required packages...
%PYTHON_EXE% -m pip install pyside6 requests websockets pywin32

if %ERRORLEVEL% neq 0 (
    echo [WARN] Failed to install dependencies. Application might fail to launch.
)

echo [INFO] Starting PySide6 Desktop GUI...
%PYTHON_EXE% "%~dp0src\ui\main_window.py"

if %ERRORLEVEL% neq 0 (
    echo [ERROR] Application crashed or exited with error code %ERRORLEVEL%
    pause
)
