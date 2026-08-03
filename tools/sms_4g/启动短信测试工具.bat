@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "SMS_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "SMS_PYTHON=.venv\Scripts\python.exe"

"%SMS_PYTHON%" -c "import PyQt5, serial" >nul 2>nul
if errorlevel 1 (
    echo [缺少依赖] 请先在当前目录执行：
    echo.
    echo     python -m pip install -r requirements.txt
    echo.
    echo 建议使用 Python 3.10，并确认安装成功后重新双击本文件。
    pause
    exit /b 1
)

"%SMS_PYTHON%" main.py
if errorlevel 1 (
    echo.
    echo 工具异常退出，请保留窗口中的错误信息。
    pause
)
endlocal
