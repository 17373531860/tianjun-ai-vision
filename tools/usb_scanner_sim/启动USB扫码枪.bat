@echo off
chcp 65001 >nul
rem ============================================================
rem USB HID 扫码枪仿真器 — Windows 一键启动
rem 首次运行自动建虚拟环境并装依赖, 之后直接起窗口。
rem ============================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [首次运行] 正在创建虚拟环境并安装 PySide6 + pynput, 请稍候...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -U pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

echo 启动 USB 扫码枪仿真器...
.venv\Scripts\python.exe usb_scanner_sim.py
pause
