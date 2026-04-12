#!/usr/bin/env python3
"""
模拟扫码器 — TCP Server 模式
后端 ScannerService 主动连接本工具的 IP:Port，
本工具按设定的条码、次数、间隔发送 barcode\r\n 给后端。

用法：
1. 启动本工具，设定监听端口（默认 9100）
2. 在检测软件 MES > 扫码器 中新建设备，IP 填本机 127.0.0.1，端口 9100
3. 后端自动连接后，状态灯变绿
4. 设置条码内容、发送次数、间隔，点击发送
"""

import sys
import socket
import threading
import time
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QDoubleSpinBox,
    QTextEdit, QGroupBox, QGridLayout, QComboBox, QCheckBox
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QColor


class SignalBridge(QObject):
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)


class ScannerSimulator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("扫码器模拟器")
        self.setMinimumSize(600, 520)

        self._server_sock = None
        self._client_sock = None
        self._running = False
        self._sending = False
        self._lock = threading.Lock()

        self._bridge = SignalBridge()
        self._bridge.log_signal.connect(self._append_log)
        self._bridge.status_signal.connect(self._update_status)

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)

        # ── 服务器设置 ──
        srv_group = QGroupBox("TCP 服务器")
        srv_lay = QGridLayout(srv_group)

        srv_lay.addWidget(QLabel("监听端口:"), 0, 0)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(9100)
        srv_lay.addWidget(self.port_spin, 0, 1)

        self.status_label = QLabel("● 未启动")
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")
        srv_lay.addWidget(self.status_label, 0, 2)

        self.btn_start = QPushButton("启动服务")
        self.btn_start.clicked.connect(self._toggle_server)
        srv_lay.addWidget(self.btn_start, 0, 3)

        layout.addWidget(srv_group)

        # ── 条码设置 ──
        code_group = QGroupBox("条码设置")
        code_lay = QGridLayout(code_group)

        code_lay.addWidget(QLabel("条码内容:"), 0, 0)
        self.barcode_input = QLineEdit("SN20260409001")
        self.barcode_input.setFont(QFont("Consolas", 11))
        code_lay.addWidget(self.barcode_input, 0, 1, 1, 3)

        code_lay.addWidget(QLabel("自增模式:"), 1, 0)
        self.auto_inc = QCheckBox("末尾数字自动递增")
        self.auto_inc.setChecked(True)
        code_lay.addWidget(self.auto_inc, 1, 1, 1, 2)

        code_lay.addWidget(QLabel("快捷预设:"), 2, 0)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems([
            "自定义",
            "SN20260409001",
            "TEST_OK_001",
            "PART-A-001",
            "60325025629970008",
        ])
        self.preset_combo.currentTextChanged.connect(self._on_preset)
        code_lay.addWidget(self.preset_combo, 2, 1, 1, 3)

        layout.addWidget(code_group)

        # ── 发送参数 ──
        send_group = QGroupBox("发送参数")
        send_lay = QGridLayout(send_group)

        send_lay.addWidget(QLabel("发送次数:"), 0, 0)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 9999)
        self.count_spin.setValue(1)
        send_lay.addWidget(self.count_spin, 0, 1)

        send_lay.addWidget(QLabel("间隔 (秒):"), 0, 2)
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.1, 60.0)
        self.interval_spin.setValue(1.0)
        self.interval_spin.setSingleStep(0.1)
        self.interval_spin.setDecimals(1)
        send_lay.addWidget(self.interval_spin, 0, 3)

        btn_row = QHBoxLayout()
        self.btn_send = QPushButton("▶  发送")
        self.btn_send.setMinimumHeight(36)
        self.btn_send.setStyleSheet(
            "QPushButton { background: #0891b2; color: white; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background: #0e7490; }"
            "QPushButton:disabled { background: #6b7280; }"
        )
        self.btn_send.clicked.connect(self._start_send)
        btn_row.addWidget(self.btn_send)

        self.btn_stop_send = QPushButton("■  停止")
        self.btn_stop_send.setMinimumHeight(36)
        self.btn_stop_send.setEnabled(False)
        self.btn_stop_send.setStyleSheet(
            "QPushButton { background: #ef4444; color: white; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background: #dc2626; }"
            "QPushButton:disabled { background: #6b7280; }"
        )
        self.btn_stop_send.clicked.connect(self._stop_send)
        btn_row.addWidget(self.btn_stop_send)

        self.btn_send_once = QPushButton("单次发送")
        self.btn_send_once.setMinimumHeight(36)
        self.btn_send_once.clicked.connect(self._send_once)
        btn_row.addWidget(self.btn_send_once)

        send_lay.addLayout(btn_row, 1, 0, 1, 4)
        layout.addWidget(send_group)

        # ── 日志 ──
        log_group = QGroupBox("日志")
        log_lay = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setMaximumHeight(180)
        log_lay.addWidget(self.log_text)

        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self.log_text.clear)
        log_lay.addWidget(btn_clear)

        layout.addWidget(log_group)

    def _on_preset(self, text):
        if text != "自定义":
            self.barcode_input.setText(text)

    def _log(self, msg):
        self._bridge.log_signal.emit(msg)

    def _append_log(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.log_text.append(f"[{ts}] {msg}")
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def _update_status(self, status):
        colors = {
            "listening": ("● 等待连接...", "orange"),
            "connected": ("● 已连接", "#10b981"),
            "stopped":   ("● 未启动", "gray"),
            "error":     ("● 错误", "red"),
        }
        text, color = colors.get(status, ("● " + status, "gray"))
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    # ── TCP 服务器 ──

    def _toggle_server(self):
        if self._running:
            self._stop_server()
        else:
            self._start_server()

    def _start_server(self):
        port = self.port_spin.value()
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.settimeout(1.0)
            self._server_sock.bind(("0.0.0.0", port))
            self._server_sock.listen(1)
        except OSError as e:
            self._log(f"启动失败: {e}")
            self._bridge.status_signal.emit("error")
            return

        self._running = True
        self.btn_start.setText("停止服务")
        self.port_spin.setEnabled(False)
        self._log(f"TCP 服务器启动，监听端口 {port}")
        self._bridge.status_signal.emit("listening")

        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()

    def _stop_server(self):
        self._running = False
        if self._client_sock:
            try:
                self._client_sock.close()
            except Exception:
                pass
            self._client_sock = None
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None
        self.btn_start.setText("启动服务")
        self.port_spin.setEnabled(True)
        self._bridge.status_signal.emit("stopped")
        self._log("服务器已停止")

    def _accept_loop(self):
        while self._running:
            try:
                client, addr = self._server_sock.accept()
                with self._lock:
                    if self._client_sock:
                        try:
                            self._client_sock.close()
                        except Exception:
                            pass
                    self._client_sock = client
                self._log(f"客户端已连接: {addr[0]}:{addr[1]}")
                self._bridge.status_signal.emit("connected")

                # 后台读取客户端发来的数据（如 LON 命令等），防止缓冲区满
                t = threading.Thread(
                    target=self._drain_client, args=(client,), daemon=True
                )
                t.start()

            except socket.timeout:
                continue
            except OSError:
                break

        if self._running:
            self._bridge.status_signal.emit("listening")

    def _drain_client(self, sock):
        """读取并丢弃后端发来的命令（LON 等），保持连接活跃"""
        try:
            sock.settimeout(2.0)
            while self._running:
                try:
                    data = sock.recv(4096)
                    if not data:
                        break
                    text = data.decode("utf-8", errors="ignore").strip()
                    if text:
                        self._log(f"← 收到: {text}")
                except socket.timeout:
                    continue
        except OSError:
            pass
        finally:
            self._log("客户端断开连接")
            with self._lock:
                if self._client_sock is sock:
                    self._client_sock = None
            self._bridge.status_signal.emit("listening")

    # ── 发送逻辑 ──

    def _get_barcode(self, index):
        """根据自增模式生成第 index 个条码"""
        base = self.barcode_input.text().strip()
        if not base:
            return "EMPTY"
        if not self.auto_inc.isChecked() or index == 0:
            return base

        # 找到末尾的数字部分并递增
        i = len(base)
        while i > 0 and base[i - 1].isdigit():
            i -= 1
        if i == len(base):
            return base  # 没有数字

        prefix = base[:i]
        num_str = base[i:]
        new_num = int(num_str) + index
        return prefix + str(new_num).zfill(len(num_str))

    def _send_data(self, barcode):
        with self._lock:
            sock = self._client_sock
        if not sock:
            self._log("⚠ 未连接客户端，无法发送")
            return False
        try:
            payload = (barcode + "\r\n").encode("utf-8")
            sock.sendall(payload)
            self._log(f"→ 发送: {barcode}")
            return True
        except OSError as e:
            self._log(f"⚠ 发送失败: {e}")
            return False

    def _send_once(self):
        barcode = self.barcode_input.text().strip() or "EMPTY"
        self._send_data(barcode)

    def _start_send(self):
        if self._sending:
            return
        self._sending = True
        self.btn_send.setEnabled(False)
        self.btn_stop_send.setEnabled(True)
        self.btn_send_once.setEnabled(False)

        count = self.count_spin.value()
        interval = self.interval_spin.value()
        self._log(f"开始批量发送: {count} 次, 间隔 {interval}s")

        t = threading.Thread(
            target=self._send_loop, args=(count, interval), daemon=True
        )
        t.start()

    def _stop_send(self):
        self._sending = False

    def _send_loop(self, count, interval):
        for i in range(count):
            if not self._sending:
                self._log("发送已中止")
                break
            barcode = self._get_barcode(i)
            if not self._send_data(barcode):
                break
            if i < count - 1 and self._sending:
                time.sleep(interval)

        self._sending = False
        self._bridge.log_signal.emit(f"批量发送完成")
        # 恢复按钮状态（通过信号安全操作 UI）
        self._bridge.status_signal.emit(
            "connected" if self._client_sock else "listening"
        )
        QTimer.singleShot(0, self._reset_send_buttons)

    def _reset_send_buttons(self):
        self.btn_send.setEnabled(True)
        self.btn_stop_send.setEnabled(False)
        self.btn_send_once.setEnabled(True)

    def closeEvent(self, event):
        self._sending = False
        self._stop_server()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = ScannerSimulator()
    win.show()
    sys.exit(app.exec_())
