@echo off
chcp 65001 >nul
rem ====== TianJun 热补丁 d: 修主程序版本读取 (修 PLUGIN_INCOMPATIBLE_VERSION 0.0.0) ======
net session >nul 2>&1
if %errorLevel% neq 0 (
  echo 需要管理员权限写入安装目录, 正在请求提权...
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
setlocal
set "INSTALL=C:\Program Files\tianjun-ai-vision\resources"
set "PY=%INSTALL%\python\python.exe"
set "VF=%INSTALL%\backend\_version.py"
echo ============================================================
echo  TianJun 热补丁 - 修复主程序版本号读取 (0.0.0 -> 真实版本)
echo ============================================================
if not exist "%PY%" (
  echo 错误: 未找到 Python: %PY%
  pause & exit /b
)
echo [1/5] 关闭天军...
taskkill /f /im "TianJun AI Vision.exe" >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8001 ^| findstr LISTENING') do taskkill /f /pid %%a >nul 2>&1
echo [2/5] 清理字节码缓存...
rd /s /q "%INSTALL%\backend\__pycache__" >nul 2>&1
echo [3/5] 备份 _version.py...
copy /y "%VF%" "%VF%.bak" >nul 2>&1
echo [4/5] 写入修复后的 _version.py...
del "%TEMP%\tj_ver.b64" >nul 2>&1
echo IiIi5Li756iL5bqP54mI5pys5Y+35ZSv5LiA5p2l5rqQ77yI6L+Q6KGM5pe25Y+q6K+777yJ44CCCgrorr7orqHlj5boiI06Ci0gKirllK/kuIDmnYPlqIHmupAqKjogYGBlbGVjdHJvbi9wYWNrYWdlLmpzb25gYCDnmoQgYGB2ZXJzaW9uYGAg5a2X5q6177yI5LiO5Y+R54mIL0NJIOWvuem9kO+8iQotIOWQjuerr+WQr+WKqOaXtuS4gOasoeivuyArIOi/m+eoi+e6p+e8k+WtmO+8iOmBv+WFjeeDrei3r+W+hOmHjeWkjSBJL0/vvIkKLSDmib7kuI3liLDmiJbop6PmnpDlpLHotKXlm57pgIAgYGAiMC4wLjAiYGAg4oCU4oCUIOiuqeS7u+S9lSBgYG1haW5fdmVyc2lvbl9taW5gYCDmo4Dmn6Xpg73lpLHotKUsCiAg55u45b2T5LqOIuaPkuS7tuezu+e7n+S4jeW3peS9nCA+IOivr+WIpOWFvOWuuSLnmoTlronlhajlpLHotKXkvqcKCuS4uuS7gOS5iOS4jeWcqCBgYGJhY2tlbmQvX19pbml0X18ucHlgYCDlhpnluLjph486Ci0g5Li754mI5pys5Y+36Lef552AIGVsZWN0cm9uL3BhY2thZ2UuanNvbiDotbAgKElubm8gU2V0dXAgLyBHaXRIdWIgQWN0aW9uIC8g5a6i5oi35py6CiAg57uf5LiA5LuO6L+Z6YeM6K+7KSwg5ZCO56uv5YaN57u05oqk5LiA5Lu95bi46YeP5b+F54S25ryC56e7Ci0g6YCa6L+H6L+Q6KGM5pe26K+75L+d6K+BIuS7o+eggSArIOmFjee9riArIOWuieijheWMhSIg5LiJ6ICF54mI5pys5LiA6Ie0CiIiIgpmcm9tIF9fZnV0dXJlX18gaW1wb3J0IGFubm90YXRpb25zCgppbXBvcnQganNvbgppbXBvcnQg>>"%TEMP%\tj_ver.b64"
echo b3MKaW1wb3J0IHJlCmZyb20gZnVuY3Rvb2xzIGltcG9ydCBscnVfY2FjaGUKZnJvbSBwYXRobGliIGltcG9ydCBQYXRoCgpfUEFDS0FHRV9KU09OID0gUGF0aChfX2ZpbGVfXykucmVzb2x2ZSgpLnBhcmVudC5wYXJlbnQgLyAiZWxlY3Ryb24iIC8gInBhY2thZ2UuanNvbiIKX0ZBTExCQUNLID0gIjAuMC4wIgpfVkVSU0lPTl9SRSA9IHJlLmNvbXBpbGUociJeXGQrXC5cZCtcLlxkKygtW2EtekEtWjAtOS4tXSspPyQiKQoKCkBscnVfY2FjaGUobWF4c2l6ZT0xKQpkZWYgZ2V0X21haW5fdmVyc2lvbigpIC0+IHN0cjoKICAgICIiIui/lOWbnuS4u+eoi+W6j+eJiOacrOWPt+Wtl+espuS4su+8jOS+i+WmgiBgYCIzLjEyLjAiYGDjgIIKCiAgICDlj5blgLzkvJjlhYjnuqcgKOS4jiBleHBvcnRfY29udGV4dC5fcmVhZF9hcHBfaW5mbyDlr7npvZApOgogICAgMS4g546v5aKD5Y+Y6YePIGBgVElBTkpVTl9BUFBfVkVSU0lPTmBgIOKAlOKAlCBFbGVjdHJvbiDlkK/liqjlkI7nq6/ml7bms6jlhaUgKGJhY2tlbmQtbWFuYWdlci5qcykuCiAgICAgICAqKuato+W8j+WuieijheWMheW/hei1sOi/meadoSoqOiDmiZPljIXlkI4gcGFja2FnZS5qc29uIOi/m+S6hiBhcHAuYXNhciwgUHl0aG9uIGZzIOivu+S4jeWIsCwKICAgICAgIOWPquiDvemdoCBlbnYg5LygLiDkuYvliY3mvI/or7vmraQgZW52IOWvvOiHtOWuouaIt+acuuaPkuS7tueJiOacrOagoemqjOaBkuS4uiAwLjAuMCDogIzor6/mi5IuCiAgICAyLiDlm57pgIDor7sg>>"%TEMP%\tj_ver.b64"
echo YGBlbGVjdHJvbi9wYWNrYWdlLmpzb25gYCDmlofku7Yg4oCU4oCUIOS7heW8gOWPkeaooeW8j+ebtOaOpSB1dmljb3JuIOWQr+WKqOaXtuWRveS4rS4KICAgIDMuIOmDveWksei0peWbnumAgCBgYCIwLjAuMCJgYO+8jOiuqeaPkuS7tiBgYG1haW5fdmVyc2lvbl9taW5gYCDmoKHpqozlpKnnhLbkuI3pgJrov4fjgIIKICAgICIiIgogICAgZW52X3ZlciA9IG9zLmVudmlyb24uZ2V0KCJUSUFOSlVOX0FQUF9WRVJTSU9OIiwgIiIpLnN0cmlwKCkKICAgIGlmIF9WRVJTSU9OX1JFLm1hdGNoKGVudl92ZXIpOgogICAgICAgIHJldHVybiBlbnZfdmVyCgogICAgdHJ5OgogICAgICAgIGlmIG5vdCBfUEFDS0FHRV9KU09OLmV4aXN0cygpOgogICAgICAgICAgICByZXR1cm4gX0ZBTExCQUNLCiAgICAgICAgZGF0YSA9IGpzb24ubG9hZHMoX1BBQ0tBR0VfSlNPTi5yZWFkX3RleHQoZW5jb2Rpbmc9InV0Zi04IikpCiAgICAgICAgdmVyID0gc3RyKGRhdGEuZ2V0KCJ2ZXJzaW9uIikgb3IgIiIpLnN0cmlwKCkKICAgICAgICBpZiBub3QgX1ZFUlNJT05fUkUubWF0Y2godmVyKToKICAgICAgICAgICAgcmV0dXJuIF9GQUxMQkFDSwogICAgICAgIHJldHVybiB2ZXIKICAgIGV4Y2VwdCAoT1NFcnJvciwganNvbi5KU09ORGVjb2RlRXJyb3IsIFVuaWNvZGVEZWNvZGVFcnJvcik6CiAgICAgICAgcmV0dXJuIF9GQUxMQkFDSwo=>>"%TEMP%\tj_ver.b64"
"%PY%" -c "import base64;open(r'%VF%','wb').write(base64.b64decode(open(r'%TEMP%\tj_ver.b64','rb').read()))"
del "%TEMP%\tj_ver.b64" >nul 2>&1
echo [5/5] 验证 (模拟注入版本):
cd /d "%INSTALL%"
set "TIANJUN_APP_VERSION=3.15.1"
"%PY%" -c "from backend._version import get_main_version as g;print('  注入3.15.1时读到:', g())"
echo ============================================================
echo  完成! 请重启 天军 AI Vision, 插件应能正常加载.
echo  (真实版本号由 Electron 启动时按安装包版本自动注入)
echo ============================================================
pause
