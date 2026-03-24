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

echo 1. 正在激活环境...
call conda activate tianjun

echo.
echo 2. 正在安装/升级 PyTorch CUDA 12.8 (支持 RTX 50 系列 Blackwell 显卡)...
echo    这可能需要几分钟时间...
echo.
pip install --retries 5 --timeout 60 torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 (
    echo 错误: PyTorch cu128 安装失败
    pause
    exit /b 1
)

echo.
echo 3. 验证 PyTorch CUDA 版本...
python -c "import torch; v=torch.__version__; c=torch.version.cuda; a=torch.cuda.get_arch_list(); print(f'PyTorch: {v}'); print(f'CUDA: {c}'); print(f'Arch: {a}'); assert 'cu128' in v or c.startswith('12.8'), f'错误: 需要 cu128, 当前为 {v}'; assert 'sm_120' in a, f'错误: 缺少 sm_120 (Blackwell) 支持'"
if errorlevel 1 (
    echo.
    echo !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    echo   错误: PyTorch 版本不正确！
    echo   需要 cu128 以支持 RTX 50 系列显卡
    echo   请手动运行:
    echo   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
    echo !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    pause
    exit /b 1
)
echo PyTorch cu128 验证通过！

echo.
echo 4. 安装 conda-pack...
pip install conda-pack

echo.
echo 5. 正在打包 conda 环境 'tianjun'...
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
echo 5-2. 正在解压环境...
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
echo 6. 正在修复路径...
cd /d "%OUTPUT_DIR%\python"
call Scripts\activate.bat
conda-unpack
call Scripts\deactivate.bat

echo.
echo 7. 最终验证打包后的 PyTorch...
cd /d "%OUTPUT_DIR%\python"
.\python.exe -c "import torch; v=torch.__version__; c=torch.version.cuda; a=torch.cuda.get_arch_list(); print(f'[打包验证] PyTorch: {v}, CUDA: {c}'); print(f'[打包验证] Arch: {a}'); ok='sm_120' in a; print(f'[打包验证] sm_120 (Blackwell): {\"OK\" if ok else \"缺失!!!\"}'); exit(0 if ok else 1)"
if errorlevel 1 (
    echo.
    echo 错误: 打包后的环境缺少 sm_120 支持！打包可能有问题。
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   打包完成！PyTorch cu128 + sm_120 已验证
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
