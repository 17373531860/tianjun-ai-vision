@echo off
chcp 65001 >nul
title 虚拟称重器

set "PYTHON="
if exist "%~dp0..\resources\python\python.exe" (
    set "PYTHON=%~dp0..\resources\python\python.exe"
) else if exist "D:\tianjun-ai-vision\resources\python\python.exe" (
    set "PYTHON=D:\tianjun-ai-vision\resources\python\python.exe"
) else if exist "E:\tianjun-ai-vision\resources\python\python.exe" (
    set "PYTHON=E:\tianjun-ai-vision\resources\python\python.exe"
) else if exist "C:\tianjun-ai-vision\resources\python\python.exe" (
    set "PYTHON=C:\tianjun-ai-vision\resources\python\python.exe"
) else (
    set "PYTHON=python"
)

echo ============================================
echo   虚拟称重器
echo   使用 Python: %PYTHON%
echo ============================================
echo.

"%PYTHON%" "%~dp0virtual_weight.pyw" %*

if errorlevel 1 (
    echo.
    echo 启动失败，请确认 Python 路径是否正确
    pause
)
