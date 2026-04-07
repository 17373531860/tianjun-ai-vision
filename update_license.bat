@echo off
chcp 936 >nul 2>&1

set "APP_NAME=����Ƽ�AI�Ӿ����ϵͳ"
set "TARGET_DIR=%APPDATA%\%APP_NAME%"
set "TARGET_FILE=%TARGET_DIR%\license.lic"
set "SOURCE_FILE=%~dp0license.lic"

echo ==========================================
echo   ����Ƽ� - ��Ȩ���¹���
echo ==========================================
echo.

if not exist "%SOURCE_FILE%" (
    echo [����] ��ǰĿ¼��δ�ҵ� license.lic �ļ�
    echo ��ȷ�� license.lic ��˽ű���ͬһĿ¼
    echo.
    pause
    exit /b 1
)

if not exist "%TARGET_DIR%" (
    echo [��ʾ] δ�ҵ�Ĭ��Ŀ¼: %TARGET_DIR%
    echo [��ʾ] ����������Ȩ�ļ�λ��...
    echo.

    set "FOUND="
    for /d %%D in ("%APPDATA%\*") do (
        if exist "%%D\license.lic" (
            if exist "%%D\machine_id.txt" (
                set "TARGET_DIR=%%D"
                set "TARGET_FILE=%%D\license.lic"
                set "FOUND=1"
                echo [�ҵ�] %%D
            )
        )
    )

    if not defined FOUND (
        echo [����] δ�ҵ�������װ����Ŀ¼
        echo ��ȷ��������������װ�����й�һ��
        echo.
        pause
        exit /b 1
    )
)

echo Դ�ļ�: %SOURCE_FILE%
echo Ŀ��: %TARGET_FILE%
echo.

if exist "%TARGET_FILE%" (
    copy /Y "%TARGET_FILE%" "%TARGET_DIR%\license.lic.bak" >nul 2>&1
    echo [����] �ѱ���ԭ��Ȩ�ļ�Ϊ license.lic.bak
)

copy /Y "%SOURCE_FILE%" "%TARGET_FILE%" >nul 2>&1
if errorlevel 1 (
    echo [����] ����ʧ�ܣ����Թ���Ա�������д˽ű�
    echo.
    pause
    exit /b 1
)

echo.
echo [�ɹ�] ��Ȩ�ļ��Ѹ��£�
echo ����������ʹ����Ȩ��Ч��
echo.
pause
