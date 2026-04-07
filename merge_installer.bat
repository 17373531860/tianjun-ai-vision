@echo off
setlocal enabledelayedexpansion
title TianJun AI Vision - Merge Tool v2.0.6 (Debug)

echo.
echo ============================================
echo   TianJun AI Vision - Merge Tool (DEBUG)
echo ============================================
echo.

echo [DEBUG] Current directory:
cd
echo.

echo [DEBUG] Listing all .part files (sorted):
dir /b /o:n *.part 2>nul
if errorlevel 1 (
    echo [ERROR] No .part files found!
    pause
    exit /b 1
)
echo.

echo [DEBUG] File count and sizes:
set count=0
set totalsize=0
for /f "delims=" %%f in ('dir /b /o:n *.part') do (
    set /a count+=1
    for %%s in ("%%f") do (
        echo   !count!. %%f  = %%~zs bytes
    )
)
echo.
echo [DEBUG] Total parts: !count!
echo.

echo [DEBUG] Building file list...
set "files="
for /f "delims=" %%f in ('dir /b /o:n *.part') do (
    if "!files!"=="" (
        set "files="%%f""
    ) else (
        set "files=!files!+"%%f""
    )
)

echo [DEBUG] Copy command will be:
echo   copy /b !files! "TianJun-AI-Vision-Setup.exe"
echo.
echo [DEBUG] Command length: 
echo !files!| find /c /v "" >nul
echo !files!> _debug_cmd.tmp
for %%a in (_debug_cmd.tmp) do echo   !files! is %%~za bytes long
del _debug_cmd.tmp 2>nul
echo.

if exist "TianJun-AI-Vision-Setup.exe" (
    echo [DEBUG] Deleting old output file...
    del /f /q "TianJun-AI-Vision-Setup.exe"
)

echo [DEBUG] Running merge now...
echo.
copy /b !files! "TianJun-AI-Vision-Setup.exe"
set "COPYERR=!errorlevel!"
echo.
echo [DEBUG] copy exit code: !COPYERR!
echo.

if exist "TianJun-AI-Vision-Setup.exe" (
    for %%a in ("TianJun-AI-Vision-Setup.exe") do (
        echo [DEBUG] Output file size: %%~za bytes = approx %%~za bytes
        echo.
        echo ============================================
        echo   [OK] Merge complete!
        echo   File: TianJun-AI-Vision-Setup.exe
        echo   Size: %%~za bytes
        echo ============================================
    )
) else (
    echo [ERROR] Output file was NOT created!
    echo.
    echo [DEBUG] Checking disk space:
    dir /-c | find "bytes free"
)

echo.
echo [DEBUG] Verifying first 4 bytes of output (should be 4D 5A for .exe):
powershell -Command "if(Test-Path 'TianJun-AI-Vision-Setup.exe'){$fs=[System.IO.File]::OpenRead('TianJun-AI-Vision-Setup.exe');$b=New-Object byte[] 4;$null=$fs.Read($b,0,4);$fs.Close(); Write-Host ('First 4 bytes: {0:X2} {1:X2} {2:X2} {3:X2}' -f $b[0],$b[1],$b[2],$b[3]); if($b[0]-eq 0x4D -and $b[1]-eq 0x5A){Write-Host '  -> Valid EXE header (MZ)' -ForegroundColor Green}else{Write-Host '  -> INVALID! Not an EXE. Parts may be in wrong order.' -ForegroundColor Red}}else{Write-Host 'File not found'}"

echo.
echo [DEBUG] Verifying first 4 bytes of part00:
for /f "delims=" %%f in ('dir /b /o:n *.part 2^>nul ^| find "part00"') do (
    powershell -Command "$b=[System.IO.File]::ReadAllBytes('%%f')[0..3]; Write-Host ('First 4 bytes of %%f: {0:X2} {1:X2} {2:X2} {3:X2}' -f $b[0],$b[1],$b[2],$b[3]); if($b[0]-eq 0x4D -and $b[1]-eq 0x5A){Write-Host '  -> Valid EXE header (MZ)' -ForegroundColor Green}else{Write-Host '  -> Not MZ header (might be OK if split differently)' -ForegroundColor Yellow}"
)

echo.
echo ============================================
echo   Copy ALL text above and send for debug
echo ============================================
echo.
endlocal
pause
