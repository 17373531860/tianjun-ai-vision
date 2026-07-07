@echo off
chcp 65001 >nul
rem ====== 自动请求管理员权限 (写入 Program Files 下的 site-packages 需要) ======
net session >nul 2>&1
if %errorLevel% neq 0 (
  echo 需要管理员权限写入安装目录, 正在请求提权...
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
setlocal
set "PY=C:\Program Files\tianjun-ai-vision\resources\python\python.exe"
set "MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
echo ============================================================
echo  TianJun 修复插件验签 - 安装 cryptography + jsonschema
echo  (清华大学 pip 镜像源)
echo ============================================================
echo.
if not exist "%PY%" (
  echo 错误: 未找到 Python 解释器:
  echo   %PY%
  echo 请确认天军已安装在默认目录; 若装在其它盘, 请用记事本打开本脚本修改 PY 路径.
  pause
  exit /b
)
echo [1/2] 正在从清华源安装 cryptography + jsonschema ...
"%PY%" -m pip install cryptography jsonschema -i %MIRROR% --trusted-host pypi.tuna.tsinghua.edu.cn
if %errorLevel% neq 0 (
  echo.
  echo 安装失败. 若提示无 pip, 或客户机无法联网, 请联系开发者改用离线 whl 包.
  pause
  exit /b
)
echo.
echo [2/2] 验证安装结果:
"%PY%" -c "import cryptography, jsonschema; print('  cryptography', cryptography.__version__); print('  jsonschema  ', jsonschema.__version__)"
echo.
echo ============================================================
echo  完成! 请关闭并重启 天军 AI Vision, 再重新上传插件.
echo ============================================================
pause
