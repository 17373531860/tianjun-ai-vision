@echo off
setlocal EnableExtensions DisableDelayedExpansion
call :main
set "PATCH_EXIT=%ERRORLEVEL%"
echo.
if "%PATCH_EXIT%"=="0" (
  echo [OK] Patch v3.53.0b installed successfully.
) else (
  echo [ERROR] Patch v3.53.0b was not installed.
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
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PATCH_DIR%install.ps1"
exit /b %ERRORLEVEL%
