"""
一键启动虚拟扫码器 A/B + 虚拟称重器（Windows/Linux 通用）

双击本文件即可启动，不需要 bat。
首次运行若缺少 PyQt5 / pyserial，会自动 pip install --user。

配置：
  - 扫码器 A: 0.0.0.0:55256（附带 WMax 三端口 55266/55276/55286 仿真）
  - 扫码器 B: 0.0.0.0:55257
  - 称重器  : 0.0.0.0:9001
"""
from __future__ import annotations

import os
import sys
import subprocess
import shutil
import time


HERE = os.path.dirname(os.path.abspath(__file__))


def _ensure_deps():
    missing = []
    try:
        import PyQt5  # noqa: F401
    except ImportError:
        missing.append("PyQt5")
    try:
        import serial  # noqa: F401
    except ImportError:
        missing.append("pyserial")
    if missing:
        print(f"[start_all] 缺少依赖 {missing}，自动 pip install --user ...",
              flush=True)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--user",
             "--disable-pip-version-check", "-q", *missing]
        )
        print("[start_all] 依赖安装完成", flush=True)


def _spawn(script: str, args: list[str], title: str):
    full = os.path.join(HERE, script)
    if not os.path.exists(full):
        print(f"[start_all] 找不到 {full}", flush=True)
        return None

    cmd = [sys.executable, full, *args]
    print(f"[start_all] 启动 {title}: {' '.join(cmd)}", flush=True)

    kwargs: dict = {"cwd": HERE}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
    else:
        kwargs["start_new_session"] = True

    try:
        return subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        print(f"[start_all] 启动 {title} 失败: {e}", flush=True)
        return None


def main():
    _ensure_deps()

    procs = []

    p = _spawn(
        "virtual_scanner.py",
        ["--port", "55256", "--name", "扫码器A",
         "--auto-start", "--wmax"],
        "扫码器A (55256 + WMax 55266/55276/55286)",
    )
    procs.append(p)
    time.sleep(1.0)

    p = _spawn(
        "virtual_scanner.py",
        ["--port", "55257", "--name", "扫码器B", "--auto-start"],
        "扫码器B (55257)",
    )
    procs.append(p)
    time.sleep(1.0)

    p = _spawn(
        "virtual_weight.py",
        ["--port", "9001", "--name", "称重器", "--auto-start"],
        "称重器 (9001)",
    )
    procs.append(p)

    live = [p for p in procs if p is not None]
    print(f"[start_all] 已启动 {len(live)} 个进程，PID 列表: "
          f"{[p.pid for p in live]}", flush=True)
    print("[start_all] 关闭各自 GUI 窗口即可停止对应模拟器。", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            input("按 Enter 退出...")
        except Exception:
            pass
        sys.exit(1)
