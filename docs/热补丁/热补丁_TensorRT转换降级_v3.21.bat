@echo off
call :main
pause
goto :eof

:main
echo ================================================================
echo  TianJun AI Vision - TensorRT Downgrade Hotfix (v3.21)
echo.
echo  Problem: client TensorRT 11.x removed the EXPLICIT_BATCH flag,
echo           which the model exporter still needs -^> TensorRT model
echo           conversion fails on client machine.
echo  Fix:     downgrade TensorRT to 10.x (compatible, flag present).
echo           Conversion needs NO source change once TRT is 10.x.
echo ================================================================
echo.

echo [STEP 0] Close TianJun software completely before continuing.
echo Press any key AFTER the software is fully closed...
pause >nul
echo.

REM ---- best-effort kill leftover processes (ignore failures) ----
taskkill /f /im tianjun-ai-vision.exe >nul 2>&1
taskkill /f /im electron.exe >nul 2>&1
taskkill /f /im python.exe >nul 2>&1

REM ---- locate install dir (contains resources\python\python.exe) ----
set "INSTALL_DIR="
if exist "D:\TJKJ-LIN\tianjun-ai-vision\resources\python\python.exe" set "INSTALL_DIR=D:\TJKJ-LIN\tianjun-ai-vision"
if exist "D:\tianjun-ai-vision\resources\python\python.exe" set "INSTALL_DIR=D:\tianjun-ai-vision"
if exist "D:\tianjun\tianjun-ai-vision\resources\python\python.exe" set "INSTALL_DIR=D:\tianjun\tianjun-ai-vision"
if exist "D:\tianjunkeji\tianjun-ai-vision\resources\python\python.exe" set "INSTALL_DIR=D:\tianjunkeji\tianjun-ai-vision"

if defined INSTALL_DIR echo Auto detected install dir: %INSTALL_DIR%
if not defined INSTALL_DIR echo Install dir NOT auto-detected.
echo.
echo If the path above is wrong or empty, type the install dir (the folder
echo that contains resources\python\python.exe), or press Enter to keep it.
set /p INSTALL_DIR=Install dir: 

set "PY=%INSTALL_DIR%\resources\python\python.exe"
if not exist "%PY%" goto :nopython
echo Using python: %PY%
echo.

echo [STEP 1] Current TensorRT version:
"%PY%" -c "import tensorrt as t; print(t.__version__)" 2>nul
echo.

echo [STEP 2] Downgrading TensorRT to 10.x ...
echo (Offline machine: put tensorrt*.whl files NEXT TO this script first.)
echo.
if exist "%~dp0tensorrt*.whl" goto :offline
goto :online

:online
echo Mode: ONLINE (installing from NVIDIA / PyPI index)
"%PY%" -m pip install --force-reinstall --no-cache-dir "tensorrt>=10,<11" --extra-index-url https://pypi.nvidia.com
goto :verify

:offline
echo Mode: OFFLINE (installing from local wheels next to this script)
"%PY%" -m pip install --force-reinstall --no-index --find-links="%~dp0" "tensorrt>=10,<11"
goto :verify

:verify
echo.
echo [STEP 3] Verifying ...
"%PY%" -c "import tensorrt as t; v=t.__version__; ok=hasattr(getattr(t,'NetworkDefinitionCreationFlag',None),'EXPLICIT_BATCH'); print('TensorRT now:', v, '| EXPLICIT_BATCH present:', ok); assert int(v.split('.')[0])==10 and ok, 'TensorRT still incompatible'"
if errorlevel 1 goto :failed

echo.
echo ===== RESULT: SUCCESS =====
echo TensorRT downgraded to 10.x.
echo Now restart TianJun software and re-run model conversion (TensorRT FP16).
goto :eof

:nopython
echo.
echo ERROR: python.exe not found at:
echo   %PY%
echo Please re-run and enter the correct install dir.
goto :eof

:failed
echo.
echo ===== RESULT: FAILED =====
echo TensorRT downgrade did not succeed (see messages above).
echo Temporary workaround: in the app choose "PyTorch FP16" model format
echo to keep running without TensorRT. Then contact support with this log.
goto :eof
