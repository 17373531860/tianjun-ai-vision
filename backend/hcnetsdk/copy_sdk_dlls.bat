@echo off
chcp 65001 >nul
echo ================================================
echo   HCNetSDK DLL Copy Script
echo   Copy Hikvision SDK DLLs to backend/hcnetsdk/lib/
echo ================================================
echo.

if "%~1"=="" (
    echo Usage: copy_sdk_dlls.bat [SDK_LIB_DIR]
    echo Example: copy_sdk_dlls.bat "C:\HCNetSDK\lib"
    echo.
    echo SDK_LIB_DIR should be the path to the SDK's library folder
    echo containing HCNetSDK.dll, PlayCtrl.dll, HCNetSDKCom\, etc.
    exit /b 1
)

set "SDK_DIR=%~1"
set "TARGET_DIR=%~dp0lib"

if not exist "%SDK_DIR%\HCNetSDK.dll" (
    echo ERROR: HCNetSDK.dll not found in "%SDK_DIR%"
    echo Please specify the correct SDK library directory.
    exit /b 1
)

echo Source: %SDK_DIR%
echo Target: %TARGET_DIR%
echo.

echo Copying core DLLs...
copy /y "%SDK_DIR%\HCNetSDK.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\HCCore.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\PlayCtrl.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\hlog.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\hpr.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\zlib1.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\NPQos.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\SuperRender.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\AudioRender.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\HmMerge.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\MP_Render.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\AudioProcess.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\HXVA.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\YUVProcess.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\OpenAL32.dll" "%TARGET_DIR%\" >nul
copy /y "%SDK_DIR%\libmmd.dll" "%TARGET_DIR%\" >nul

echo Copying crypto/SSL DLLs...
if exist "%SDK_DIR%\libcrypto-3-x64.dll" copy /y "%SDK_DIR%\libcrypto-3-x64.dll" "%TARGET_DIR%\" >nul
if exist "%SDK_DIR%\libssl-3-x64.dll" copy /y "%SDK_DIR%\libssl-3-x64.dll" "%TARGET_DIR%\" >nul
if exist "%SDK_DIR%\libcrypto-1_1-x64.dll" copy /y "%SDK_DIR%\libcrypto-1_1-x64.dll" "%TARGET_DIR%\" >nul
if exist "%SDK_DIR%\libssl-1_1-x64.dll" copy /y "%SDK_DIR%\libssl-1_1-x64.dll" "%TARGET_DIR%\" >nul

echo Copying HCNetSDKCom directory...
if exist "%SDK_DIR%\HCNetSDKCom" (
    xcopy /y /e /i "%SDK_DIR%\HCNetSDKCom" "%TARGET_DIR%\HCNetSDKCom\" >nul
)

echo.
echo Done! Files copied to %TARGET_DIR%
echo.
dir /b "%TARGET_DIR%"
echo.
pause
