@echo off
title OBS Comment Bridge Server
echo ==============================================
echo OBS Comment Bridge Startup Script
echo ==============================================

set PYTHON_EXE="C:\Users\keymi\AppData\Local\Programs\Python\Python312\python.exe"

if not exist %PYTHON_EXE% (
    echo [ERROR] Python 3.12 was not found at %PYTHON_EXE%
    echo Please install Python 3.12 or adjust the python path in run.bat.
    pause
    exit /b 1
)

echo [INFO] Installing/Verifying python dependencies...
%PYTHON_EXE% -m pip install fastapi uvicorn websockets requests python-multipart

if %ERRORLEVEL% neq 0 (
    echo [WARN] Failed to install dependencies. The server might fail to start.
)

echo [INFO] Starting local Web server on port 8000...
echo [INFO] Opening dashboard in browser...
start "" "http://localhost:8000"

%PYTHON_EXE% -m uvicorn main:app --host 127.0.0.1 --port 8000

pause
