@echo off
setlocal EnableExtensions DisableDelayedExpansion
call :main
set "PATCH_EXIT=%ERRORLEVEL%"
echo.
if "%PATCH_EXIT%"=="0" (
  echo [OK] All v3.53.0b content hashes are exact.
) else (
  echo [NOT_PATCHED] Content check failed. Send this screen to support.
)
echo.
pause
exit /b %PATCH_EXIT%

:main
set "PATCH_DIR=%~dp0"
if not exist "%PATCH_DIR%install.ps1" (
  echo [ERROR] Missing install.ps1.
  exit /b 10
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PATCH_DIR%install.ps1" -CheckOnly
exit /b %ERRORLEVEL%
