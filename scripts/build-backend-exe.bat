@echo off
REM 使用 PyInstaller 将后端打包为可执行文件
REM 这种方式不需要打包整个 conda 环境

echo ==========================================
echo   使用 PyInstaller 打包后端
echo ==========================================
echo.

REM 激活 conda 环境
call %USERPROFILE%\anaconda3\Scripts\activate.bat tianjun
if errorlevel 1 (
    call %USERPROFILE%\miniconda3\Scripts\activate.bat tianjun
)

REM 安装 PyInstaller
pip install pyinstaller

REM 打包后端
cd /d "%~dp0..\backend"

pyinstaller --noconfirm --onedir --console ^
    --name "tianjun-backend" ^
    --add-data "api;api" ^
    --add-data "core;core" ^
    --add-data "db;db" ^
    --add-data "models;models" ^
    --add-data "schemas;schemas" ^
    --add-data "services;services" ^
    --hidden-import uvicorn.logging ^
    --hidden-import uvicorn.loops ^
    --hidden-import uvicorn.loops.auto ^
    --hidden-import uvicorn.protocols ^
    --hidden-import uvicorn.protocols.http ^
    --hidden-import uvicorn.protocols.http.auto ^
    --hidden-import uvicorn.protocols.websockets ^
    --hidden-import uvicorn.protocols.websockets.auto ^
    --hidden-import uvicorn.lifespan ^
    --hidden-import uvicorn.lifespan.on ^
    --collect-all ultralytics ^
    --collect-all torch ^
    --collect-all cv2 ^
    main.py

echo.
echo 打包完成！输出目录: backend\dist\tianjun-backend
pause
