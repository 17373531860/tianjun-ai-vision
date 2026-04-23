@echo off
chcp 65001 >nul
title ��������豸 -> ���� exe

rem =====================================================
rem  �� Windows ��˫�����ű��������������� exe��
rem    tools\dist\����ɨ����.exe
rem    tools\dist\���������.exe
rem  exe ����ȫ��������PyQt5 / pyserial / Python runtime����
rem  ��ֱ�Ӹ��Ƶ��κ� Windows ����˫�����С�
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
echo.

echo [1/3] ��װ������� (pyinstaller/PyQt5/pyserial) ...
"%PYTHON%" -m pip install --disable-pip-version-check -q pyinstaller PyQt5 pyserial
if errorlevel 1 (
    echo [ʧ��] ������װʧ�ܣ�����������ֶ�ִ�У�
    echo    %PYTHON% -m pip install pyinstaller PyQt5 pyserial
    pause
    exit /b 1
)

echo [2/3] ��� virtual_scanner.py / virtual_weight.py ...
echo.
"%PYTHON%" "%~dp0build_tools.py"
if errorlevel 1 (
    echo.
    echo [ʧ��] ���ʧ�ܣ��鿴����Ĵ�����־��
    pause
    exit /b 1
)

echo.
echo [3/3] ��ɣ�
echo.
echo ============================================
echo   ���Ŀ¼: %~dp0dist
echo     ����ɨ����.exe
echo     ���������.exe
echo.
echo   ʹ�÷�ʽ��ֱ�Ӱ������� exe �������� Windows
echo   ���ԣ�ͬһ����������˫���������У�����Ҫ
echo   Python �� pip��
echo ============================================
echo.

rem �� dist Ŀ¼
if exist "%~dp0dist" start "" explorer "%~dp0dist"

pause
