@echo off
chcp 65001 >nul
setlocal
set "PY=C:\Program Files\tianjun-ai-vision\resources\python\python.exe"
set "PKG=%~1"
if "%PKG%"=="" (
  echo.
  echo 请把 .tjvplugin 插件文件拖到本 bat 图标上运行
  echo.
  pause
  exit /b
)
echo ============================================================
echo  TianJun 插件签名诊断 DIAG2
echo ============================================================
set "B64=aW1wb3J0IHN5cywgb3MsIGpzb24sIHppcGZpbGUsIGhhc2hsaWIKZnJvbSBwYXRobGliIGltcG9ydCBQYXRoCgpJTlNUQUxMID0gciJDOlxQcm9ncmFtIEZpbGVzXHRpYW5qdW4tYWktdmlzaW9uXHJlc291cmNlcyIKc3lzLnBhdGguaW5zZXJ0KDAsIElOU1RBTEwpCgpwa2cgPSBQYXRoKHN5cy5hcmd2WzFdKSBpZiBsZW4oc3lzLmFyZ3YpID4gMSBlbHNlIE5vbmUKaWYgbm90IHBrZyBvciBub3QgcGtnLmV4aXN0cygpOgogICAgcHJpbnQoIltESUFHMl0gRVJST1I6IOacquaJvuWIsOaPkuS7tuWMhSwg6K+35oqKIC50anZwbHVnaW4g5ouW5YiwIGJhdCDkuIoiKQogICAgc3lzLmV4aXQoMSkKCndpdGggemlwZmlsZS5aaXBGaWxlKHBrZykgYXMgemY6CiAgICBuYW1lcyA9IHpmLm5hbWVsaXN0KCkKICAgIG1yYXcgPSB6Zi5yZWFkKCJwbHVnaW4uanNvbiIpCiAgICBzaWdyYXcgPSB6Zi5yZWFkKCJzaWduYXR1cmUuYmluIikKCnByaW50KCJbRElBRzJdIHBrZyAgICA9IiwgcGtnLm5hbWUpCnByaW50KCJbRElBRzJdIHppcF9uICA9IiwgbGVuKG5hbWVzKSkKcHJpbnQoIltESUFHMl0gcGpyYXcgID0iLCBoYXNobGliLnNoYTI1NihtcmF3KS5oZXhkaWdlc3QoKSkKCmZyb20gYmFja2VuZC5wbHVnaW5fc3lzdGVtLl9wbHVnaW5fY29tbW9uIGltcG9ydCAoCiAgICBjYW5vbmljYWxfanNvbiwgcGFyc2Vfc2lnbmF0dXJlX2Jsb2IsIGNhbGNfcHVia2V5X2ZpbmdlcnByaW50LCByc2FfdmVyaWZ5X3BzcywKKQpmcm9tIGJhY2tlbmQucGx1Z2luX3N5c3RlbS5wbHVnaW5fcHVibGljX2tleXMgaW1wb3J0IFBMVUdJTl9QVUJMSUNfS0VZUwoKbWFuaWZlc3QgPSBqc29uLmxvYWRzKG1yYXcpCmNqID0gY2Fub25pY2FsX2pzb24obWFuaWZlc3QpCnNpZyA9IHBhcnNlX3NpZ25hdHVyZV9ibG9iKHNpZ3JhdykKcGVtID0gUExVR0lOX1BVQkxJQ19LRVlTWzBdWyJwZW0iXQpwZW0gPSBwZW0gaWYgaXNpbnN0YW5jZShwZW0sIGJ5dGVzKSBlbHNlIHBlbS5lbmNvZGUoKQoKcHJpbnQoIltESUFHMl0gY2Fub24gID0iLCBoYXNobGliLnNoYTI1NihjaikuaGV4ZGlnZXN0KCkpCnByaW50KCJbRElBRzJdIHNpZ3NoYSA9IiwgaGFzaGxpYi5zaGEyNTYoc2lnLnJzYV9zaWduYXR1cmUpLmhleGRpZ2VzdCgpKQpwcmludCgiW0RJQUcyXSBzaWdmcCAgPSIsIHNpZy5wdWJsaWNfa2V5X2ZpbmdlcnByaW50LmhleCgpKQpwcmludCgiW0RJQUcyXSBrZXlmcCAgPSIsIGNhbGNfcHVia2V5X2ZpbmdlcnByaW50KHBlbSkuaGV4KCkpCnByaW50KCJbRElBRzJdIFZFUklGWSA9IiwgcnNhX3ZlcmlmeV9wc3MoY2osIHNpZy5yc2Ffc2lnbmF0dXJlLCBwZW0pKQoKdHJ5OgogICAgaW1wb3J0IGNyeXB0b2dyYXBoeQogICAgcHJpbnQoIltESUFHMl0gY3J5cHRvID0iLCBjcnlwdG9ncmFwaHkuX192ZXJzaW9uX18pCmV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgIHByaW50KCJbRElBRzJdIGNyeXB0byA9ID8iLCBlKQoKcHJpbnQoIltESUFHMl0gcHl2ZXIgID0iLCBzeXMudmVyc2lvbi5zcGxpdCgpWzBdKQo="
"%PY%" -c "import base64,os,sys;sys.argv=['d',os.environ['PKG']];exec(base64.b64decode(os.environ['B64']))"
echo ============================================================
pause
