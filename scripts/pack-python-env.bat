@echo off
REM Windows 环境下打包 Python 环境的脚本
REM 请在安装了 Anaconda/Miniconda 的 Windows 系统上运行

setlocal enabledelayedexpansion

echo ==========================================
echo   Python 环境打包脚本 (Windows)
echo ==========================================
echo.

REM 获取脚本目录
set SCRIPT_DIR=%~dp0
set PROJECT_DIR=%SCRIPT_DIR%..
set OUTPUT_DIR=%PROJECT_DIR%\python-env

REM 初始化 conda
call %USERPROFILE%\anaconda3\Scripts\activate.bat
if errorlevel 1 (
    call %USERPROFILE%\miniconda3\Scripts\activate.bat
    if errorlevel 1 (
        echo 错误: 无法找到 Anaconda/Miniconda 安装
        pause
        exit /b 1
    )
)

REM 检查环境是否存在
conda env list | findstr /C:"tianjun" >nul
if errorlevel 1 (
    echo 错误: conda 环境 'tianjun' 不存在
    echo 请先创建环境: conda create -n tianjun python=3.10
    pause
    exit /b 1
)

echo 1. 正在激活环境并安装 conda-pack...
call conda activate tianjun
pip install conda-pack

echo.
echo 2. 正在打包 conda 环境 'tianjun'...
echo    这可能需要几分钟时间...
echo.

REM 创建输出目录
if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

REM 使用 conda-pack 打包
conda-pack -n tianjun -o "%OUTPUT_DIR%\tianjun-env.tar.gz" --force
if errorlevel 1 (
    echo 错误: 打包失败
    pause
    exit /b 1
)

echo.
echo 3. 正在解压环境...
if exist "%OUTPUT_DIR%\python" rmdir /s /q "%OUTPUT_DIR%\python"
mkdir "%OUTPUT_DIR%\python"

REM 使用 Python 解压 (Windows 没有原生 tar 支持)
python -c "import tarfile; tarfile.open('%OUTPUT_DIR%/tianjun-env.tar.gz').extractall('%OUTPUT_DIR%/python')"
if errorlevel 1 (
    echo 错误: 解压失败
    pause
    exit /b 1
)

echo.
echo 4. 正在修复路径...
cd /d "%OUTPUT_DIR%\python"
call Scripts\activate.bat
conda-unpack
call Scripts\deactivate.bat

echo.
echo ==========================================
echo   打包完成！
echo ==========================================
echo.
echo 打包文件: %OUTPUT_DIR%\tianjun-env.tar.gz
echo 解压目录: %OUTPUT_DIR%\python
echo.

REM 显示大小
echo 环境大小:
dir "%OUTPUT_DIR%\tianjun-env.tar.gz" | findstr /C:"tianjun-env.tar.gz"
echo.

pause
