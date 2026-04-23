@echo off
chcp 65001 >nul
title ��������ɨ����

rem =====================================================
rem  ���� 1 ̨����ɨ������0.0.0.0:55256��������������
rem =====================================================

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
echo   ʹ�� Python: %PYTHON%
echo ============================================

"%PYTHON%" -c "import PyQt5" 2>nul
if errorlevel 1 (
    echo.
    echo [����ȱʧ] �״�������Ҫ��װ PyQt5...
    "%PYTHON%" -m pip install --disable-pip-version-check -q PyQt5
    if errorlevel 1 (
        echo [��װʧ��] ���ֶ�ִ�У�%PYTHON% -m pip install PyQt5
        pause
        exit /b 1
    )
    echo [������װ���]
)

start "ɨ����" "%PYTHON%" "%~dp0virtual_scanner.pyw" --port 55256 --name ɨ���� --auto-start

echo.
echo ============================================
echo   ɨ����������  TCP 0.0.0.0:55256
echo ============================================
timeout /t 3 /nobreak >nul
