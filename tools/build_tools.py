"""
打包虚拟设备工具为 Windows 单文件可执行程序

依赖：pip install pyinstaller PyQt5 pyserial
运行：python build_tools.py

生成：
  dist/虚拟扫码器.exe   — 双击即用，无需 Python
  dist/虚拟称重器.exe   — 双击即用，无需 Python
"""
import subprocess
import sys
import os

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def build(script, name):
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", name,
        "--distpath", os.path.join(TOOLS_DIR, "dist"),
        "--workpath", os.path.join(TOOLS_DIR, "build"),
        "--specpath", os.path.join(TOOLS_DIR, "build"),
        "--hidden-import", "PyQt5.sip",
        "--collect-submodules", "serial",
        os.path.join(TOOLS_DIR, script),
    ]

    print(f"\n{'=' * 60}")
    print(f"  打包: {script} → {name}.exe")
    print(f"{'=' * 60}")
    subprocess.run(cmd, check=True)
    exe_path = os.path.join(TOOLS_DIR, "dist", f"{name}.exe")
    size_mb = os.path.getsize(exe_path) / 1024 / 1024 if os.path.exists(exe_path) else 0
    print(f"  完成: {exe_path}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    build("virtual_scanner.py", "虚拟扫码器")
    build("virtual_weight.py", "虚拟称重器")

    dist = os.path.join(TOOLS_DIR, "dist")
    print(f"\n{'=' * 60}")
    print(f"  全部完成！")
    print(f"  输出目录: {dist}")
    print(f"  直接复制到 Windows 电脑双击运行即可")
    print(f"{'=' * 60}")
