@echo off
REM v3.10.2: 天军 AI 视觉检测系统 — Windows 现场 machineId 诊断工具
REM 用法: 双击运行, 或在 cmd 里执行
REM 输出: 屏幕显示 + 当前目录生成 machineid-report-<hostname>-<时间戳>.txt

setlocal enabledelayedexpansion
cd /d "%~dp0"

REM 查找 Electron 自带的 node.exe (打包后路径)
set NODE_EXE=
if exist "%~dp0..\..\..\app.asar.unpacked\electron\node\node.exe" (
    set NODE_EXE=%~dp0..\..\..\app.asar.unpacked\electron\node\node.exe
)
if "%NODE_EXE%"=="" if exist "%~dp0..\node\node.exe" set NODE_EXE=%~dp0..\node\node.exe
if "%NODE_EXE%"=="" (
    where node >nul 2>&1 && set NODE_EXE=node
)

if "%NODE_EXE%"=="" (
    echo ERROR: 找不到 node.exe
    echo 请使用以下方式之一:
    echo   1. 安装 Node.js: https://nodejs.org/
    echo   2. 把脚本放在天军安装目录的 resources\app.asar.unpacked\electron\scripts\ 下
    pause
    exit /b 1
)

echo 使用 Node: %NODE_EXE%
echo.

"%NODE_EXE%" "%~dp0diagnose-machine-id.js"

echo.
echo ====================================================
echo  诊断完成. 请把上面输出截图或把生成的 .txt 文件发给技术支持.
echo ====================================================
pause
