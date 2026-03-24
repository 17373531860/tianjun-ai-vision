@echo off
REM 完整的应用构建脚本 (Windows)
REM 此脚本将执行所有打包步骤

setlocal enabledelayedexpansion

echo ==========================================
echo   天军科技AI视觉检测系统 - 构建脚本
echo ==========================================
echo.

set SCRIPT_DIR=%~dp0
set PROJECT_DIR=%SCRIPT_DIR%..

cd /d "%PROJECT_DIR%"

REM 检查 Node.js
where node >nul 2>nul
if errorlevel 1 (
    echo 错误: 未安装 Node.js
    echo 请从 https://nodejs.org 下载安装
    pause
    exit /b 1
)

echo Node.js 版本:
node --version
echo.

REM ========== 步骤 1: 构建前端 ==========
echo ==========================================
echo 步骤 1: 构建前端
echo ==========================================
echo.

cd frontend
call npm install
if errorlevel 1 (
    echo 错误: npm install 失败
    pause
    exit /b 1
)

call npm run build
if errorlevel 1 (
    echo 错误: 前端构建失败
    pause
    exit /b 1
)
cd ..

echo 前端构建完成！
echo.

REM ========== 步骤 2: 打包 Python 环境 ==========
echo ==========================================
echo 步骤 2: 打包 Python 环境
echo ==========================================
echo.

REM 如果已有旧环境，检查 PyTorch 版本是否正确
set NEED_REPACK=0
if not exist "python-env\python\python.exe" (
    set NEED_REPACK=1
) else (
    echo 检查已有环境的 PyTorch 版本...
    "python-env\python\python.exe" -c "import torch; assert 'sm_120' in torch.cuda.get_arch_list(), 'sm_120 missing'" >nul 2>nul
    if errorlevel 1 (
        echo 已有环境缺少 sm_120 (Blackwell) 支持，需要重新打包
        rmdir /s /q "python-env\python"
        set NEED_REPACK=1
    ) else (
        echo 已有环境 PyTorch cu128 验证通过，跳过打包
    )
)
if !NEED_REPACK!==1 (
    call scripts\pack-python-env.bat
    if errorlevel 1 (
        echo 错误: Python 环境打包失败
        pause
        exit /b 1
    )
)
echo.

REM ========== 步骤 3: 安装 Electron 依赖 ==========
echo ==========================================
echo 步骤 3: 安装 Electron 依赖
echo ==========================================
echo.

cd electron
call npm install
if errorlevel 1 (
    echo 错误: Electron 依赖安装失败
    pause
    exit /b 1
)
cd ..

echo Electron 依赖安装完成！
echo.

REM ========== 步骤 4: 构建安装程序 ==========
echo ==========================================
echo 步骤 4: 构建安装程序
echo ==========================================
echo.

cd electron
call npm run build:win
if errorlevel 1 (
    echo 错误: 安装程序构建失败
    pause
    exit /b 1
)
cd ..

echo.
echo ==========================================
echo   构建完成！
echo ==========================================
echo.
echo 安装程序位于: electron\dist\
echo.

pause
