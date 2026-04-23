虚拟模拟器使用说明（副机 Windows）
================================

一、启动（二选一）
------------------

方案 A：双击 start_all.pyw（最简单）
   前提：Windows 已装 Python 3.10+（python.org 或 Anaconda 均可）。
   双击后会自动 pip install PyQt5 / pyserial，然后依次弹出
   3 个模拟器 GUI 窗口（扫码器A / 扫码器B / 称重器），自动开始
   监听以下端口：
     扫码器A : 0.0.0.0:55256  + WMax 三端口 55266 / 55276 / 55286
     扫码器B : 0.0.0.0:55257
     称重器  : 0.0.0.0:9001

方案 B：把 .bat.txt 改名去掉 .txt 后缀，再双击 bat
   例：启动全部模拟器.bat.txt  →  启动全部模拟器.bat
   （微信/QQ 传输时会过滤 .bat，所以多了一层 .txt 后缀）

二、打独立 exe（一次打包，副机零依赖）
-------------------------------------

在任一装了 Python 的 Windows 上：
  1. 把 打包便携版.bat.txt 改名去掉 .txt 后缀
  2. 双击 打包便携版.bat
  3. 产物：dist\虚拟扫码器.exe  +  dist\虚拟称重器.exe
     这两个 exe 拷到任何 Windows 机器双击即跑，不需要 Python。

三、在 tianjun 里连接
---------------------

MES → 扫码器 → 新增
  名称  : 任意
  IP    : 127.0.0.1（本机运行模拟器）或开发机 IP
  端口  : 55256 或 55257
  绑定工位：工位 1 / 2

MES → 外部设备 → 新增 TCP 传感器
  名称  : 称重器
  协议  : TCP 直连 (tcp)
  IP    : 127.0.0.1 或开发机 IP
  端口  : 9001

四、故障排查
------------

- 没有 Python：去 https://www.python.org/downloads/ 装 3.10+，
  安装时勾选 "Add Python to PATH"。
- pip install 失败：手动执行 python -m pip install PyQt5 pyserial
- bat 被杀毒软件拦截：用方案 A 的 start_all.pyw，不需要 bat。
- 扫码器在 tianjun 里显示"错误"：刷新列表或删除重加。
