"""
虚拟扫码器 — 模拟真实扫码器的 TCP 服务端

原理：真实扫码器在 IP:55256 上开放 TCP 服务端，
我们的软件作为客户端连上去，扫码器扫到码后发送 "条码\r\n"。
"""
import sys
import socket
import threading
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QSpinBox, QGroupBox,
    QListWidget, QSplitter, QCheckBox, QComboBox, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QIcon, QPalette, QColor

DARK_STYLE = """
QMainWindow { background: #1a1a2e; }
QGroupBox {
    background: #16213e; border: 1px solid #0f3460; border-radius: 8px;
    margin-top: 14px; padding: 16px 12px 12px 12px;
    color: #e0e0e0; font-weight: bold; font-size: 13px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 14px; padding: 0 6px;
    color: #00d2ff;
}
QLabel { color: #b0b0b0; font-size: 12px; }
QLineEdit {
    background: #0f3460; border: 1px solid #1a5276; border-radius: 5px;
    color: #ffffff; padding: 6px 10px; font-size: 13px;
    selection-background-color: #00d2ff;
}
QLineEdit:focus { border: 1px solid #00d2ff; }
QSpinBox, QDoubleSpinBox {
    background: #0f3460; border: 1px solid #1a5276; border-radius: 5px;
    color: #ffffff; padding: 4px 8px; font-size: 13px;
}
QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #00d2ff; }
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1a5276, stop:1 #0f3460);
    border: 1px solid #1a5276; border-radius: 6px;
    color: #e0e0e0; padding: 7px 18px; font-size: 12px; font-weight: bold;
}
QPushButton:hover { background: #1a5276; border-color: #00d2ff; color: #ffffff; }
QPushButton:pressed { background: #0f3460; }
QPushButton#sendBtn {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #00d2ff, stop:1 #0099cc);
    color: #000000; font-size: 15px; border: none; min-height: 44px;
}
QPushButton#sendBtn:hover { background: #33ddff; }
QPushButton#sendBtn:pressed { background: #0099cc; }
QTextEdit {
    background: #0a0a1a; border: 1px solid #0f3460; border-radius: 5px;
    color: #00ff88; font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px; padding: 6px;
}
QListWidget {
    background: #0f3460; border: 1px solid #1a5276; border-radius: 5px;
    color: #00d2ff; font-size: 12px; padding: 4px;
}
QListWidget::item { padding: 4px 8px; border-radius: 3px; }
QListWidget::item:selected { background: #1a5276; }
QCheckBox { color: #b0b0b0; font-size: 12px; spacing: 6px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 3px;
    border: 1px solid #1a5276; background: #0f3460; }
QCheckBox::indicator:checked { background: #00d2ff; border-color: #00d2ff; }
QComboBox {
    background: #0f3460; border: 1px solid #1a5276; border-radius: 5px;
    color: #ffffff; padding: 4px 8px; font-size: 12px;
}
QComboBox:hover { border-color: #00d2ff; }
QComboBox QAbstractItemView { background: #16213e; color: #ffffff;
    selection-background-color: #1a5276; border: 1px solid #0f3460; }
QSplitter::handle { background: #0f3460; width: 2px; }
"""


class ScannerServer(QMainWindow):
    log_signal = pyqtSignal(str)
    client_changed = pyqtSignal()

    def __init__(self, default_port=55256, default_ip="0.0.0.0", name=""):
        super().__init__()
        self._default_port = default_port
        self._default_ip = default_ip
        self._name = name
        title = "虚拟扫码器"
        if name:
            title += f"  [ {name} ]"
        self.setWindowTitle(title)
        self.setMinimumSize(700, 560)

        self.server_socket = None
        self.clients = []
        self.lock = threading.Lock()
        self.running = False

        self._build_ui()
        self.log_signal.connect(self._append_log)
        self.client_changed.connect(self._refresh_clients)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        # 头部标题
        header = QLabel(f"📡  虚拟扫码器  —  {self._name or 'TCP Server'}")
        header.setStyleSheet("color: #00d2ff; font-size: 18px; font-weight: bold; padding: 4px 0;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        # 服务器配置
        srv_group = QGroupBox("服务配置")
        srv_layout = QHBoxLayout(srv_group)
        srv_layout.addWidget(QLabel("名称"))
        self.name_input = QLineEdit(self._name or "扫码器")
        self.name_input.setFixedWidth(100)
        srv_layout.addWidget(self.name_input)
        srv_layout.addWidget(QLabel("绑定 IP"))
        self.ip_input = QLineEdit(self._default_ip)
        self.ip_input.setFixedWidth(140)
        srv_layout.addWidget(self.ip_input)
        srv_layout.addWidget(QLabel("端口"))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(self._default_port)
        self.port_spin.setFixedWidth(90)
        srv_layout.addWidget(self.port_spin)
        self.btn_start = QPushButton("▶  启动")
        self.btn_start.setFixedWidth(100)
        self.btn_start.clicked.connect(self.toggle_server)
        srv_layout.addWidget(self.btn_start)
        self.status_label = QLabel("●  未启动")
        self.status_label.setStyleSheet("color: #666; font-weight: bold; font-size: 13px;")
        srv_layout.addWidget(self.status_label)
        srv_layout.addStretch()
        layout.addWidget(srv_group)

        # 中间区域
        splitter = QSplitter(Qt.Horizontal)

        # 左：发送面板
        send_group = QGroupBox("发送条码")
        send_layout = QVBoxLayout(send_group)
        send_layout.setSpacing(10)

        self.barcode_input = QLineEdit()
        self.barcode_input.setPlaceholderText("输入条码后按 Enter 发送...")
        self.barcode_input.returnPressed.connect(self.send_barcode)
        self.barcode_input.setFont(QFont("Consolas", 16))
        self.barcode_input.setMinimumHeight(44)
        send_layout.addWidget(self.barcode_input)

        self.btn_send = QPushButton("发  送")
        self.btn_send.setObjectName("sendBtn")
        self.btn_send.clicked.connect(self.send_barcode)
        send_layout.addWidget(self.btn_send)

        # 自动递增
        auto_row = QHBoxLayout()
        self.auto_increment = QCheckBox("自动递增")
        self.auto_increment.setChecked(True)
        auto_row.addWidget(self.auto_increment)
        auto_row.addWidget(QLabel("前缀"))
        self.prefix_input = QLineEdit("SN-")
        self.prefix_input.setFixedWidth(70)
        auto_row.addWidget(self.prefix_input)
        auto_row.addWidget(QLabel("序号"))
        self.counter_spin = QSpinBox()
        self.counter_spin.setRange(1, 999999)
        self.counter_spin.setValue(1)
        self.counter_spin.setFixedWidth(80)
        auto_row.addWidget(self.counter_spin)
        self.btn_gen = QPushButton("生成")
        self.btn_gen.clicked.connect(self.gen_barcode)
        auto_row.addWidget(self.btn_gen)
        auto_row.addStretch()
        send_layout.addLayout(auto_row)

        # 定时发送
        timer_row = QHBoxLayout()
        self.auto_send = QCheckBox("定时自动发送")
        timer_row.addWidget(self.auto_send)
        timer_row.addWidget(QLabel("间隔"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 3600)
        self.interval_spin.setValue(5)
        self.interval_spin.setSuffix(" 秒")
        timer_row.addWidget(self.interval_spin)
        timer_row.addStretch()
        send_layout.addLayout(timer_row)

        # 预设
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("预设"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems([
            "-- 选择 --", "BOX-001", "BOX-002", "BOX-003",
            "SN-20260416-001", "SN-20260416-002",
            "PART-A-001", "PART-B-001",
        ])
        self.preset_combo.currentTextChanged.connect(self._on_preset)
        preset_row.addWidget(self.preset_combo)
        preset_row.addStretch()
        send_layout.addLayout(preset_row)

        splitter.addWidget(send_group)

        # 右：客户端列表
        client_group = QGroupBox("已连接客户端")
        client_layout = QVBoxLayout(client_group)
        self.client_list = QListWidget()
        self.client_list.setMinimumWidth(180)
        client_layout.addWidget(self.client_list)
        self.client_count_label = QLabel("0 个连接")
        self.client_count_label.setStyleSheet("color: #666; font-size: 11px;")
        self.client_count_label.setAlignment(Qt.AlignCenter)
        client_layout.addWidget(self.client_count_label)
        splitter.addWidget(client_group)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        # 日志
        log_group = QGroupBox("通信日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)

        # 定时器
        self.timer = QTimer()
        self.timer.timeout.connect(self._auto_send_tick)
        self.auto_send.toggled.connect(self._toggle_timer)

    def toggle_server(self):
        if self.running:
            self.stop_server()
        else:
            self.start_server()

    def start_server(self):
        bind_ip = self.ip_input.text().strip() or "0.0.0.0"
        port = self.port_spin.value()
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((bind_ip, port))
            self.server_socket.listen(5)
            self.server_socket.settimeout(1.0)
            self.running = True
            name = self.name_input.text().strip() or "扫码器"
            self.setWindowTitle(f"虚拟扫码器  [ {name} ]  —  {bind_ip}:{port}")
            self.btn_start.setText("■  停止")
            self.port_spin.setEnabled(False)
            self.ip_input.setEnabled(False)
            self.name_input.setEnabled(False)
            self.status_label.setText(f"●  监听中  {bind_ip}:{port}")
            self.status_label.setStyleSheet("color: #00ff88; font-weight: bold; font-size: 13px;")
            self.log_signal.emit(f"服务器启动，监听 {bind_ip}:{port}")
            threading.Thread(target=self._accept_loop, daemon=True).start()
        except OSError as e:
            self.log_signal.emit(f"启动失败: {e}")
            self.status_label.setText(f"●  启动失败")
            self.status_label.setStyleSheet("color: #ff4444; font-weight: bold; font-size: 13px;")

    def stop_server(self):
        self.running = False
        with self.lock:
            for c in self.clients:
                try:
                    c["socket"].close()
                except Exception:
                    pass
            self.clients.clear()
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
        self.btn_start.setText("▶  启动")
        self.port_spin.setEnabled(True)
        self.ip_input.setEnabled(True)
        self.name_input.setEnabled(True)
        self.status_label.setText("●  已停止")
        self.status_label.setStyleSheet("color: #666; font-weight: bold; font-size: 13px;")
        self.client_changed.emit()
        self.log_signal.emit("服务器已停止")

    def _accept_loop(self):
        while self.running:
            try:
                client_sock, addr = self.server_socket.accept()
                with self.lock:
                    self.clients.append({
                        "socket": client_sock,
                        "addr": f"{addr[0]}:{addr[1]}",
                        "time": datetime.now().strftime("%H:%M:%S"),
                    })
                self.log_signal.emit(f"✔ 客户端连接: {addr[0]}:{addr[1]}")
                self.client_changed.emit()
                threading.Thread(target=self._client_reader,
                                 args=(client_sock, addr), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _client_reader(self, sock, addr):
        sock.settimeout(2.0)
        try:
            while self.running:
                try:
                    data = sock.recv(1024)
                    if not data:
                        break
                    text = data.decode("utf-8", errors="ignore").strip()
                    if text:
                        self.log_signal.emit(f"← [{addr[0]}:{addr[1]}] {text}")
                except socket.timeout:
                    continue
                except OSError:
                    break
        finally:
            with self.lock:
                self.clients = [c for c in self.clients if c["socket"] != sock]
            try:
                sock.close()
            except Exception:
                pass
            self.log_signal.emit(f"✘ 客户端断开: {addr[0]}:{addr[1]}")
            self.client_changed.emit()

    def send_barcode(self):
        barcode = self.barcode_input.text().strip()
        if not barcode:
            return
        data = (barcode + "\r\n").encode("utf-8")
        sent_count = 0
        with self.lock:
            dead = []
            for c in self.clients:
                try:
                    c["socket"].sendall(data)
                    sent_count += 1
                except OSError:
                    dead.append(c)
            for d in dead:
                self.clients.remove(d)
        if dead:
            self.client_changed.emit()

        if sent_count > 0:
            self.log_signal.emit(f"→ 发送: {barcode}  ({sent_count} 客户端)")
        else:
            self.log_signal.emit(f"→ 发送: {barcode}  (无客户端)")

        if self.auto_increment.isChecked():
            self.counter_spin.setValue(self.counter_spin.value() + 1)
            self.gen_barcode()

    def gen_barcode(self):
        prefix = self.prefix_input.text()
        num = self.counter_spin.value()
        self.barcode_input.setText(f"{prefix}{num:04d}")

    def _on_preset(self, text):
        if text != "-- 选择 --":
            self.barcode_input.setText(text)

    def _toggle_timer(self, checked):
        if checked:
            self.timer.start(self.interval_spin.value() * 1000)
            self.log_signal.emit(f"⏱ 定时发送已开启，间隔 {self.interval_spin.value()} 秒")
        else:
            self.timer.stop()
            self.log_signal.emit("⏱ 定时发送已关闭")

    def _auto_send_tick(self):
        if self.auto_increment.isChecked():
            self.gen_barcode()
        self.send_barcode()

    def _append_log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{ts}] {msg}")

    def _refresh_clients(self):
        self.client_list.clear()
        with self.lock:
            for c in self.clients:
                self.client_list.addItem(f"  {c['addr']}    {c['time']}")
            count = len(self.clients)
        self.client_count_label.setText(f"{count} 个连接")
        self.client_count_label.setStyleSheet(
            f"color: {'#00ff88' if count > 0 else '#666'}; font-size: 11px;")

    def closeEvent(self, event):
        self.stop_server()
        event.accept()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="虚拟扫码器")
    parser.add_argument("--port", type=int, default=55256, help="监听端口")
    parser.add_argument("--name", type=str, default="", help="设备名称")
    parser.add_argument("--multi", type=int, default=0, help="同时启动多个实例")
    parser.add_argument("--ips", type=str, default="",
                        help="多实例绑定 IP，逗号分隔 (如 192.168.0.100,192.168.0.101)")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)

    windows = []
    if args.multi >= 2:
        ips = [s.strip() for s in args.ips.split(",") if s.strip()] if args.ips else []
        for i in range(args.multi):
            name = f"扫码器 {chr(65 + i)}"
            w = ScannerServer(default_port=args.port,
                              default_ip=ips[i] if i < len(ips) else "0.0.0.0",
                              name=name)
            w.move(80 + i * 720, 80)
            w.show()
            windows.append(w)
    else:
        w = ScannerServer(default_port=args.port, name=args.name)
        w.show()
        windows.append(w)

    sys.exit(app.exec_())
