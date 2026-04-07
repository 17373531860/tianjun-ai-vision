@echo off
chcp 936 >nul 2>&1

echo ==========================================
echo   ����Ƽ� - ��ȡ������
echo ==========================================
echo.

set "APP_NAME=����Ƽ�AI�Ӿ����ϵͳ"
set "TARGET_DIR=%APPDATA%\%APP_NAME%"
set "ID_FILE=%TARGET_DIR%\machine_id.txt"

if exist "%ID_FILE%" (
    echo ������:
    echo.
    type "%ID_FILE%"
    echo.
    echo.
    echo �뽫�˻����뷢������֧��
    echo.
    pause
    exit /b 0
)

echo [��ʾ] Ĭ��·��δ�ҵ�����������...
echo.

for /d %%D in ("%APPDATA%\*") do (
    if exist "%%D\machine_id.txt" (
        if exist "%%D\license.lic" (
            echo ������:
            echo.
            type "%%D\machine_id.txt"
            echo.
            echo.
            echo �뽫�˻����뷢������֧��
            echo.
            pause
            exit /b 0
        )
    )
)

echo [����] δ�ҵ��������ļ�
echo ��ȷ�������Ѱ�װ�����й�һ��
echo.
pause
