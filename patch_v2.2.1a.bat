@echo off
setlocal enabledelayedexpansion
title TianJun AI Vision v2.2.1a Patch

echo.
echo ============================================
echo   TianJun AI Vision v2.2.1a Patch
echo ============================================
echo.

set "INSTALL_DIR="

for %%p in (
    "D:\tianjun-ai-vision"
    "D:\tianjun\tianjun-ai-vision"
    "D:\tianjunkeji\tianjun-ai-vision"
    "C:\tianjun-ai-vision"
    "E:\tianjun-ai-vision"
    "C:\Program Files\TianJun AI Vision"
) do (
    if exist "%%~p\resources\python\python.exe" (
        set "INSTALL_DIR=%%~p"
        goto :found
    )
)

echo Install path not found.
echo.
:ask_path
set /p "USER_PATH=Enter install path: "
if "!USER_PATH!"=="" (
    echo [ERROR] No path entered.
    pause
    exit /b 1
)
set "INSTALL_DIR=!USER_PATH!"
if not exist "!INSTALL_DIR!\resources\python\python.exe" (
    echo [ERROR] python.exe not found at !INSTALL_DIR!\resources\python\
    echo.
    goto :ask_path
)

:found
echo Using: !INSTALL_DIR!
echo.

set "PYTHON=!INSTALL_DIR!\resources\python\python.exe"
set "SITE_PKG=!INSTALL_DIR!\resources\python\Lib\site-packages"
set "BACKEND=!INSTALL_DIR!\resources\backend"

echo [1/5] Closing app...
taskkill /f /im "TianJun AI Vision.exe" >nul 2>&1
taskkill /f /im "tianjun-ai-vision.exe" >nul 2>&1
timeout /t 2 /nobreak >nul
echo       Done.
echo.

echo [2/5] Uninstall and deep clean...
"!PYTHON!" -m pip uninstall -y opencv-python opencv-python-headless opencv-contrib-python opencv-contrib-python-headless numpy 2>nul
echo       Removing all residual files...
if exist "!SITE_PKG!\cv2" rd /s /q "!SITE_PKG!\cv2" 2>nul
if exist "!SITE_PKG!\numpy" rd /s /q "!SITE_PKG!\numpy" 2>nul
if exist "!SITE_PKG!\numpy.libs" rd /s /q "!SITE_PKG!\numpy.libs" 2>nul
for /d %%d in ("!SITE_PKG!\opencv_python-*") do rd /s /q "%%d" 2>nul
for /d %%d in ("!SITE_PKG!\opencv_python_headless-*") do rd /s /q "%%d" 2>nul
for /d %%d in ("!SITE_PKG!\opencv_contrib_python-*") do rd /s /q "%%d" 2>nul
for /d %%d in ("!SITE_PKG!\opencv_contrib_python_headless-*") do rd /s /q "%%d" 2>nul
for /d %%d in ("!SITE_PKG!\numpy-*") do rd /s /q "%%d" 2>nul
for /d %%d in ("!SITE_PKG!\numpy.libs-*") do rd /s /q "%%d" 2>nul
echo       Clearing pycache...
for /d /r "!SITE_PKG!" %%d in (__pycache__) do rd /s /q "%%d" 2>nul
echo       Done.
echo.

echo [3/5] Install numpy + opencv (clean)...
"!PYTHON!" -m pip install numpy==1.26.4 --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple
if !errorlevel! neq 0 goto :fail_numpy
echo       numpy OK. Installing opencv...
"!PYTHON!" -m pip install opencv-contrib-python==4.10.0.84 --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple
if !errorlevel! neq 0 goto :fail_opencv
echo       Done.
echo.

echo [4/5] Verify...
"!PYTHON!" -c "import numpy as np, cv2; img=np.zeros((100,100,3),dtype=np.uint8); cv2.putText(img,'OK',(10,50),cv2.FONT_HERSHEY_SIMPLEX,1,(255,255,255),2); print('numpy='+np.__version__+' cv2='+cv2.__version__); print('putText OK')"
if !errorlevel! neq 0 goto :fail_verify
echo       Done.
echo.

echo [5/5] Deploy hotfix...
set "PATCH_DIR=%~dp0"
if exist "!PATCH_DIR!hotfix.py" (
    copy /Y "!PATCH_DIR!hotfix.py" "!BACKEND!\hotfix.py" >nul
    echo       hotfix.py deployed.
)
if exist "!PATCH_DIR!source.py" (
    copy /Y "!PATCH_DIR!source.py" "!BACKEND!\api\source.py" >nul
    echo       source.py deployed.
)
echo       Done.
echo.

echo ============================================
echo   Patch applied successfully!
echo   Please restart the application.
echo ============================================
echo.
pause
exit /b 0

:fail_numpy
echo [ERROR] numpy install failed!
goto :fail

:fail_opencv
echo [ERROR] opencv install failed!
goto :fail

:fail_verify
echo [ERROR] Verify failed! Try rebooting and run again.
goto :fail

:fail
echo.
echo ============================================
echo   Patch FAILED. Send output to developer.
echo ============================================
echo.
pause
exit /b 1
