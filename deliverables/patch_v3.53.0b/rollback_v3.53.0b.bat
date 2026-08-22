@echo off
setlocal EnableExtensions DisableDelayedExpansion
call :main
set "PATCH_EXIT=%ERRORLEVEL%"
echo.
if "%PATCH_EXIT%"=="0" (
  echo [OK] Exact pre-v3.53.0b files were restored.
) else (
  echo [ERROR] Rollback was not completed.
)
echo.
pause
exit /b %PATCH_EXIT%

:main
set "PATCH_DIR=%~dp0"
if not exist "%PATCH_DIR%rollback.ps1" (
  echo [ERROR] Missing rollback.ps1.
  exit /b 10
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PATCH_DIR%rollback.ps1"
exit /b %ERRORLEVEL%
