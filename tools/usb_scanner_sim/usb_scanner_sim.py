# -*- coding: utf-8 -*-
"""USB HID 扫码枪仿真器 — 外部独立工具 (不在天军主程序内).

用途:
    在没有真实扫码枪的情况下, 用键盘注入模拟"USB 键盘扫码枪"的行为, 驱动天军软件
    走真实加工链路 (扫码 → 序列号加工补 '-' → 查 MES → 检测). 与软件内置的"虚拟扫码器"
    不同 —— 这是 *系统级键盘注入*, 天军前端的全局键盘捕获 (useScanGun, 速度启发式 + 回车
    终止符) 会把它当成真实 USB HID 扫码枪。

真实还原:
    工厂那把扫码枪读不出特殊符号 '-'。所以注入前自动剥掉 '-' (和空白), 敲进去的是
    "扫码枪实际能读到的串", 由天军软件按配置把 '-' 补回去。界面会显示"实际注入串"。

依赖: PySide6 + pynput (见 requirements.txt)。Windows 双击「启动USB扫码枪.bat」一键起。

注意:
    - 注入是往"当前获得焦点的窗口"。点「模拟扫码」后有倒计时, 期间请把焦点切到天军窗口。
    - Linux 下需 X11 (Wayland 可能拦键盘注入); Windows 原生支持。
"""
import re
import sys
import time

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMainWindow, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

try:
    from pynput.keyboard import Controller, Key
except Exception as e:  # pragma: no cover - 依赖缺失时给清晰提示
    print(f"[致命] 未能导入 pynput: {e}\n请先安装依赖: pip install -r requirements.txt")
    raise


# 扫码枪读不出的字符 (现场: '-')。注入前剥掉, 模拟扫码枪实际读到的串。
_STRIP_CHARS = "-－—–"


def to_scanned(raw: str) -> str:
    """把"显示用工单号"转成"扫码枪实际能读到的串": 去掉连字符类符号 + 去空白。"""
    s = re.sub(r"\s+", "", raw or "")
    for ch in _STRIP_CHARS:
        s = s.replace(ch, "")
    return s


class ScanWorker(QThread):
    """后台线程: 倒计时 (给用户切焦点) → 逐字符注入 → 末尾终止符。不阻塞 UI。"""

    tick = Signal(int)          # 倒计时剩余秒
    injected = Signal(str)      # 实际注入完成的串
    failed = Signal(str)        # 异常信息

    def __init__(self, code: str, delay_sec: int, interval_ms: int, terminator: str):
        super().__init__()
        self._code = code
        self._delay_sec = max(0, int(delay_sec))
        self._interval = max(0, int(interval_ms)) / 1000.0
        self._terminator = terminator
        self._kb = Controller()

    def run(self) -> None:
        try:
            for remain in range(self._delay_sec, 0, -1):
                self.tick.emit(remain)
                time.sleep(1)
            self.tick.emit(0)
            for ch in self._code:
                self._kb.type(ch)
                if self._interval:
                    time.sleep(self._interval)
            if self._terminator == "enter":
                self._kb.press(Key.enter)
                self._kb.release(Key.enter)
            elif self._terminator == "tab":
                self._kb.press(Key.tab)
                self._kb.release(Key.tab)
            self.injected.emit(self._code)
        except Exception as e:
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    # 现场真实样例 (显示号 → 扫码枪读到的串): '-' 补在第 12 位之后
    PRESETS = [
        "JOB150300021-3",
        "JOB150300021-31",
        "JOB150300021-313",
        "JOB123",  # 工单不存在 (测查无单 → 提示重扫)
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("USB 扫码枪仿真器 — 天军 AI 视觉检测")
        self.resize(560, 520)
        self._worker = None

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel("工单号 / 标签号 (照实填, 含 '-' 也行, 注入时自动剥掉)"))
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("例: JOB150300021-3")
        self.code_edit.returnPressed.connect(self.start_scan)
        layout.addWidget(self.code_edit)

        self.preview = QLabel("实际注入串: —")
        self.preview.setStyleSheet("color:#0a7;")
        layout.addWidget(self.preview)
        self.code_edit.textChanged.connect(self._update_preview)

        row = QHBoxLayout()
        row.addWidget(QLabel("注入前倒计时(秒)"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 30)
        self.delay_spin.setValue(3)
        row.addWidget(self.delay_spin)
        row.addWidget(QLabel("按键间隔(ms)"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(0, 200)
        self.interval_spin.setValue(8)
        row.addWidget(self.interval_spin)
        row.addWidget(QLabel("终止符"))
        self.term_combo = QComboBox()
        self.term_combo.addItem("回车 Enter", "enter")
        self.term_combo.addItem("Tab", "tab")
        self.term_combo.addItem("无", "none")
        row.addWidget(self.term_combo)
        layout.addLayout(row)

        self.scan_btn = QPushButton("模拟扫码 (倒计时内请切到天军窗口)")
        self.scan_btn.clicked.connect(self.start_scan)
        layout.addWidget(self.scan_btn)

        self.status = QLabel("就绪")
        self.status.setStyleSheet("color:#888;")
        layout.addWidget(self.status)

        layout.addWidget(QLabel("常用预设 (双击 = 填入)"))
        self.preset_list = QListWidget()
        self.preset_list.addItems(self.PRESETS)
        self.preset_list.itemDoubleClicked.connect(
            lambda it: self.code_edit.setText(it.text()))
        layout.addWidget(self.preset_list)

        layout.addWidget(QLabel("注入历史"))
        self.history = QListWidget()
        layout.addWidget(self.history)

        self._update_preview()

    def _update_preview(self) -> None:
        scanned = to_scanned(self.code_edit.text())
        self.preview.setText(f"实际注入串: {scanned or '—'}")

    def start_scan(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        code = to_scanned(self.code_edit.text())
        if not code:
            self.status.setText("请输入工单号 / 标签号")
            return
        self.scan_btn.setEnabled(False)
        self._worker = ScanWorker(
            code,
            self.delay_spin.value(),
            self.interval_spin.value(),
            self.term_combo.currentData(),
        )
        self._worker.tick.connect(self._on_tick)
        self._worker.injected.connect(self._on_injected)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(lambda: self.scan_btn.setEnabled(True))
        self._worker.start()

    def _on_tick(self, remain: int) -> None:
        if remain > 0:
            self.status.setText(f"{remain} 秒后注入… 请把焦点切到天军窗口")
        else:
            self.status.setText("正在注入…")

    def _on_injected(self, code: str) -> None:
        self.status.setText(f"已注入: {code}")
        self.history.insertItem(0, f"{time.strftime('%H:%M:%S')}  {code}")

    def _on_failed(self, msg: str) -> None:
        self.status.setText(f"注入失败: {msg}")


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
