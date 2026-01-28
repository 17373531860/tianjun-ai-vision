@echo off
setlocal enabledelayedexpansion

echo ============================================
echo   Tianjun AI Vision System - Auto Fix
echo ============================================
echo.

REM Set install directory - MODIFY THIS IF NEEDED
set "INSTALL_DIR=D:\tianjunkeji\tianjun-ai-vision"

REM Check multiple common paths
if not exist "%INSTALL_DIR%" (
    set "INSTALL_DIR=C:\Program Files\tianjun-ai-vision"
)
if not exist "%INSTALL_DIR%" (
    set "INSTALL_DIR=%LOCALAPPDATA%\Programs\tianjun-ai-vision"
)
if not exist "%INSTALL_DIR%" (
    echo [ERROR] Cannot find install directory automatically.
    echo Please enter the install path manually:
    set /p INSTALL_DIR="Install path: "
)

if not exist "%INSTALL_DIR%" (
    echo [ERROR] Directory not found: %INSTALL_DIR%
    pause
    exit /b 1
)

set "RESOURCES_DIR=%INSTALL_DIR%\resources"
set "PYTHON_EXE=%RESOURCES_DIR%\python\python.exe"

echo [INFO] Install dir: %INSTALL_DIR%
echo [INFO] Resources: %RESOURCES_DIR%
echo.

REM Check Python exists
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found: %PYTHON_EXE%
    pause
    exit /b 1
)

REM Restore backup if needed
if exist "%RESOURCES_DIR%\app.asar.backup" (
    if exist "%RESOURCES_DIR%\app.asar" (
        echo [INFO] Backup exists, restoring...
        copy /Y "%RESOURCES_DIR%\app.asar.backup" "%RESOURCES_DIR%\app.asar" >nul
        echo [INFO] Restored from backup
    )
)

REM Remove old app folder if exists
if exist "%RESOURCES_DIR%\app" (
    echo [INFO] Removing old app folder...
    rmdir /s /q "%RESOURCES_DIR%\app" 2>nul
)

REM Remove disabled asar if exists
if exist "%RESOURCES_DIR%\app.asar.disabled" (
    del /f /q "%RESOURCES_DIR%\app.asar.disabled" 2>nul
)

echo.
echo [STEP] Running patch...
"%PYTHON_EXE%" "%~dp0patch.py" "%RESOURCES_DIR%"

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Patch failed!
    pause
    exit /b 1
)

echo.
echo ============================================
echo   DONE! Please restart the application.
echo ============================================
echo.
pause
