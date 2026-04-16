"""
虚拟称重器 — 模拟真实称重器的串口通信

支持两种模式：
1. Modbus ASCII 从机 — 被动响应主机读取请求
2. 连续发送 — 按间隔主动推送重量数据

也支持 TCP 模式（软件配置为 TCP 协议时可直接连接）。
"""
import sys
import socket
import struct
import threading
import time
import random
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QSpinBox, QGroupBox,
    QDoubleSpinBox, QComboBox, QCheckBox, QRadioButton, QSlider, QFrame,
    QTabWidget,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont

DARK_STYLE = """
QMainWindow { background: #1a1a2e; }
QTabWidget::pane { background: #1a1a2e; border: 1px solid #0f3460; border-radius: 6px; }
QTabBar::tab {
    background: #16213e; color: #888; border: 1px solid #0f3460;
    padding: 8px 20px; margin-right: 2px; border-top-left-radius: 6px;
    border-top-right-radius: 6px; font-weight: bold;
}
QTabBar::tab:selected { background: #0f3460; color: #00d2ff; }
QGroupBox {
    background: #16213e; border: 1px solid #0f3460; border-radius: 8px;
    margin-top: 14px; padding: 16px 12px 12px 12px;
    color: #e0e0e0; font-weight: bold; font-size: 13px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 14px; padding: 0 6px; color: #00d2ff;
}
QLabel { color: #b0b0b0; font-size: 12px; }
QLineEdit {
    background: #0f3460; border: 1px solid #1a5276; border-radius: 5px;
    color: #ffffff; padding: 6px 10px; font-size: 13px;
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
QPushButton:hover { background: #1a5276; border-color: #00d2ff; color: #fff; }
QPushButton:pressed { background: #0f3460; }
QPushButton#presetBtn {
    padding: 6px 10px; font-size: 11px; min-width: 55px;
}
QPushButton#presetBtn:hover { background: #1a5276; border-color: #ffaa00; }
QTextEdit {
    background: #0a0a1a; border: 1px solid #0f3460; border-radius: 5px;
    color: #00ff88; font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px; padding: 6px;
}
QCheckBox { color: #b0b0b0; font-size: 12px; spacing: 6px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 3px;
    border: 1px solid #1a5276; background: #0f3460; }
QCheckBox::indicator:checked { background: #00d2ff; border-color: #00d2ff; }
QRadioButton { color: #b0b0b0; font-size: 12px; spacing: 6px; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 8px;
    border: 1px solid #1a5276; background: #0f3460; }
QRadioButton::indicator:checked { background: #00d2ff; border-color: #00d2ff; }
QComboBox {
    background: #0f3460; border: 1px solid #1a5276; border-radius: 5px;
    color: #ffffff; padding: 4px 8px; font-size: 12px;
}
QComboBox:hover { border-color: #00d2ff; }
QComboBox QAbstractItemView { background: #16213e; color: #fff;
    selection-background-color: #1a5276; border: 1px solid #0f3460; }
QSlider::groove:horizontal {
    height: 6px; background: #0f3460; border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 18px; height: 18px; margin: -6px 0;
    background: #00d2ff; border-radius: 9px;
}
QSlider::sub-page:horizontal { background: #00d2ff; border-radius: 3px; }
"""


class WeightSimulator(QMainWindow):
    log_signal = pyqtSignal(str)

    def __init__(self, default_ip="0.0.0.0", default_port=9001, name=""):
        super().__init__()
        self._default_ip = default_ip
        self._default_port = default_port
        self._name = name
        title = "虚拟称重器"
        if name:
            title += f"  [ {name} ]"
        self.setWindowTitle(title)
        self.setMinimumSize(640, 600)

        self.serial_conn = None
        self.tcp_server = None
        self.tcp_clients = []
        self.tcp_lock = threading.Lock()
        self.running = False

        self._build_ui()
        self.log_signal.connect(self._append_log)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        header = QLabel(f"⚖  虚拟称重器  —  {self._name or 'Weight Simulator'}")
        header.setStyleSheet("color: #ffaa00; font-size: 18px; font-weight: bold; padding: 4px 0;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        # 重量设置
        weight_group = QGroupBox("重量模拟")
        wl = QVBoxLayout(weight_group)

        display_row = QHBoxLayout()
        self.weight_display = QLabel("0.0")
        self.weight_display.setStyleSheet(
            "color: #ffaa00; font-size: 42px; font-weight: bold; "
            "font-family: 'Consolas', monospace; padding: 8px 0;")
        self.weight_display.setAlignment(Qt.AlignCenter)
        display_row.addWidget(self.weight_display)
        unit_label = QLabel("g")
        unit_label.setStyleSheet("color: #888; font-size: 20px; padding-top: 16px;")
        display_row.addWidget(unit_label)
        display_row.addStretch()

        spin_col = QVBoxLayout()
        spin_col.addWidget(QLabel("精确输入"))
        self.weight_spin = QDoubleSpinBox()
        self.weight_spin.setRange(-99999, 99999)
        self.weight_spin.setDecimals(1)
        self.weight_spin.setValue(0)
        self.weight_spin.setSingleStep(0.1)
        self.weight_spin.setFont(QFont("Consolas", 14))
        self.weight_spin.setMinimumHeight(36)
        self.weight_spin.setFixedWidth(180)
        self.weight_spin.valueChanged.connect(self._on_weight_changed)
        spin_col.addWidget(self.weight_spin)
        display_row.addLayout(spin_col)
        wl.addLayout(display_row)

        self.weight_slider = QSlider(Qt.Horizontal)
        self.weight_slider.setRange(0, 500000)
        self.weight_slider.setValue(0)
        self.weight_slider.valueChanged.connect(
            lambda v: self.weight_spin.setValue(v / 10.0))
        wl.addWidget(self.weight_slider)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("快捷"))
        for val in [0, 100, 500, 1000, 2500, 5000, 10000, 15000, 25000]:
            lbl = f"{val/1000:.1f}kg" if val >= 1000 else f"{val}g"
            btn = QPushButton(lbl)
            btn.setObjectName("presetBtn")
            btn.clicked.connect(lambda _, v=val: self.weight_spin.setValue(v))
            preset_row.addWidget(btn)
        preset_row.addStretch()
        wl.addLayout(preset_row)

        jitter_row = QHBoxLayout()
        self.jitter_check = QCheckBox("随机波动 ±")
        jitter_row.addWidget(self.jitter_check)
        self.jitter_spin = QDoubleSpinBox()
        self.jitter_spin.setRange(0, 100)
        self.jitter_spin.setValue(0.5)
        self.jitter_spin.setDecimals(1)
        self.jitter_spin.setSuffix(" g")
        self.jitter_spin.setFixedWidth(90)
        jitter_row.addWidget(self.jitter_spin)
        jitter_row.addStretch()
        wl.addLayout(jitter_row)

        layout.addWidget(weight_group)

        # 连接方式 — Tab
        tabs = QTabWidget()

        # Tab 1: TCP
        tcp_tab = QWidget()
        tcp_layout = QVBoxLayout(tcp_tab)
        tcp_layout.setSpacing(8)

        tcp_conn = QHBoxLayout()
        tcp_conn.addWidget(QLabel("绑定 IP"))
        self.tcp_ip = QLineEdit(self._default_ip)
        self.tcp_ip.setFixedWidth(140)
        tcp_conn.addWidget(self.tcp_ip)
        tcp_conn.addWidget(QLabel("端口"))
        self.tcp_port = QSpinBox()
        self.tcp_port.setRange(1, 65535)
        self.tcp_port.setValue(self._default_port)
        self.tcp_port.setFixedWidth(90)
        tcp_conn.addWidget(self.tcp_port)
        self.tcp_btn = QPushButton("▶  启动")
        self.tcp_btn.setFixedWidth(100)
        self.tcp_btn.clicked.connect(self.toggle_tcp)
        tcp_conn.addWidget(self.tcp_btn)
        self.tcp_status = QLabel("●  未启动")
        self.tcp_status.setStyleSheet("color: #666; font-weight: bold;")
        tcp_conn.addWidget(self.tcp_status)
        tcp_conn.addStretch()
        tcp_layout.addLayout(tcp_conn)

        tcp_mode = QHBoxLayout()
        self.tcp_mode_continuous = QRadioButton("连续发送")
        self.tcp_mode_continuous.setChecked(True)
        tcp_mode.addWidget(self.tcp_mode_continuous)
        tcp_mode.addWidget(QLabel("间隔"))
        self.tcp_interval = QSpinBox()
        self.tcp_interval.setRange(100, 60000)
        self.tcp_interval.setValue(500)
        self.tcp_interval.setSuffix(" ms")
        self.tcp_interval.setFixedWidth(100)
        tcp_mode.addWidget(self.tcp_interval)
        tcp_mode.addWidget(QLabel("格式"))
        self.tcp_format = QComboBox()
        self.tcp_format.addItems(["纯数值 (1530.0)", "带单位 (1530.0g)", "CSV (weight,1530.0)"])
        tcp_mode.addWidget(self.tcp_format)
        tcp_mode.addStretch()
        tcp_layout.addLayout(tcp_mode)

        tcp_layout.addStretch()
        tabs.addTab(tcp_tab, "TCP 模式")

        # Tab 2: 串口
        serial_tab = QWidget()
        serial_layout = QVBoxLayout(serial_tab)
        serial_layout.setSpacing(8)

        ser_conn = QHBoxLayout()
        ser_conn.addWidget(QLabel("串口"))
        self.ser_port = QComboBox()
        self.ser_port.setEditable(True)
        self.ser_port.setFixedWidth(160)
        self._scan_ports()
        ser_conn.addWidget(self.ser_port)
        self.ser_refresh = QPushButton("刷新")
        self.ser_refresh.clicked.connect(self._scan_ports)
        ser_conn.addWidget(self.ser_refresh)
        ser_conn.addWidget(QLabel("波特率"))
        self.ser_baud = QComboBox()
        self.ser_baud.addItems(["9600", "19200", "38400", "115200"])
        ser_conn.addWidget(self.ser_baud)
        self.ser_btn = QPushButton("▶  连接")
        self.ser_btn.setFixedWidth(100)
        self.ser_btn.clicked.connect(self.toggle_serial)
        ser_conn.addWidget(self.ser_btn)
        self.ser_status = QLabel("●  未连接")
        self.ser_status.setStyleSheet("color: #666; font-weight: bold;")
        ser_conn.addWidget(self.ser_status)
        ser_conn.addStretch()
        serial_layout.addLayout(ser_conn)

        ser_mode = QHBoxLayout()
        self.ser_mode_modbus = QRadioButton("Modbus ASCII 从机")
        self.ser_mode_modbus.setChecked(True)
        ser_mode.addWidget(self.ser_mode_modbus)
        self.ser_mode_cont = QRadioButton("连续发送")
        ser_mode.addWidget(self.ser_mode_cont)
        ser_mode.addStretch()
        serial_layout.addLayout(ser_mode)

        ser_params = QHBoxLayout()
        ser_params.addWidget(QLabel("从站 ID"))
        self.slave_spin = QSpinBox()
        self.slave_spin.setRange(1, 247)
        self.slave_spin.setValue(1)
        self.slave_spin.setFixedWidth(70)
        ser_params.addWidget(self.slave_spin)
        ser_params.addWidget(QLabel("字节序"))
        self.byte_order = QComboBox()
        self.byte_order.addItems(["H4H3L2L1 (大端)", "L2L1H4H3 (小端)"])
        ser_params.addWidget(self.byte_order)
        ser_params.addWidget(QLabel("间隔"))
        self.ser_interval = QSpinBox()
        self.ser_interval.setRange(100, 60000)
        self.ser_interval.setValue(500)
        self.ser_interval.setSuffix(" ms")
        self.ser_interval.setFixedWidth(100)
        ser_params.addWidget(self.ser_interval)
        ser_params.addStretch()
        serial_layout.addLayout(ser_params)

        serial_layout.addStretch()
        tabs.addTab(serial_tab, "串口模式")

        layout.addWidget(tabs)

        # 日志
        log_group = QGroupBox("通信日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(130)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)

    # ---- 重量 ----

    def _get_weight(self) -> float:
        w = self.weight_spin.value()
        if self.jitter_check.isChecked():
            w += random.uniform(-self.jitter_spin.value(), self.jitter_spin.value())
        return round(w, 1)

    def _on_weight_changed(self, val):
        self.weight_display.setText(f"{val:.1f}")
        self.weight_slider.blockSignals(True)
        self.weight_slider.setValue(int(val * 10))
        self.weight_slider.blockSignals(False)

    def _weight_to_registers(self, weight: float):
        raw = int(weight * 10)
        if raw < 0:
            raw += 0x100000000
        big_endian = self.byte_order.currentIndex() == 0
        if big_endian:
            return (raw >> 16) & 0xFFFF, raw & 0xFFFF
        else:
            return raw & 0xFFFF, (raw >> 16) & 0xFFFF

    @staticmethod
    def _lrc(data: bytes) -> int:
        return (-sum(data)) & 0xFF

    def _build_modbus_response(self, slave, hi, lo):
        pdu = bytes([slave, 0x03, 4,
                     (hi >> 8) & 0xFF, hi & 0xFF,
                     (lo >> 8) & 0xFF, lo & 0xFF])
        lrc = self._lrc(pdu)
        return f":{pdu.hex().upper()}{lrc:02X}\r\n".encode("ascii")

    def _format_weight(self, w, fmt_idx=0):
        if fmt_idx == 0:
            return f"{w:.1f}"
        elif fmt_idx == 1:
            return f"{w:.1f}g"
        else:
            return f"weight,{w:.1f}"

    # ---- TCP 模式 ----

    def toggle_tcp(self):
        if self.running and self.tcp_server:
            self.stop_tcp()
        else:
            self.start_tcp()

    def start_tcp(self):
        ip = self.tcp_ip.text().strip() or "0.0.0.0"
        port = self.tcp_port.value()
        try:
            self.tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.tcp_server.bind((ip, port))
            self.tcp_server.listen(5)
            self.tcp_server.settimeout(1.0)
            self.running = True
            self.tcp_btn.setText("■  停止")
            self.tcp_ip.setEnabled(False)
            self.tcp_port.setEnabled(False)
            self.tcp_status.setText(f"●  监听中  {ip}:{port}")
            self.tcp_status.setStyleSheet("color: #00ff88; font-weight: bold;")
            self.log_signal.emit(f"TCP 服务启动: {ip}:{port}")
            threading.Thread(target=self._tcp_accept_loop, daemon=True).start()
        except OSError as e:
            self.log_signal.emit(f"TCP 启动失败: {e}")
            self.tcp_status.setText("●  启动失败")
            self.tcp_status.setStyleSheet("color: #ff4444; font-weight: bold;")

    def stop_tcp(self):
        self.running = False
        with self.tcp_lock:
            for c in self.tcp_clients:
                try:
                    c.close()
                except Exception:
                    pass
            self.tcp_clients.clear()
        if self.tcp_server:
            try:
                self.tcp_server.close()
            except Exception:
                pass
            self.tcp_server = None
        self.tcp_btn.setText("▶  启动")
        self.tcp_ip.setEnabled(True)
        self.tcp_port.setEnabled(True)
        self.tcp_status.setText("●  已停止")
        self.tcp_status.setStyleSheet("color: #666; font-weight: bold;")
        self.log_signal.emit("TCP 已停止")

    def _tcp_accept_loop(self):
        while self.running and self.tcp_server:
            try:
                client_sock, addr = self.tcp_server.accept()
                with self.tcp_lock:
                    self.tcp_clients.append(client_sock)
                self.log_signal.emit(f"✔ TCP 客户端: {addr[0]}:{addr[1]}")
                threading.Thread(target=self._tcp_client_handler,
                                 args=(client_sock, addr), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _tcp_client_handler(self, sock, addr):
        interval = self.tcp_interval.value() / 1000.0
        fmt_idx = self.tcp_format.currentIndex()
        try:
            while self.running:
                w = self._get_weight()
                line = self._format_weight(w, fmt_idx) + "\r\n"
                try:
                    sock.sendall(line.encode("utf-8"))
                    self.log_signal.emit(f"→ [{addr[0]}] {line.strip()}")
                except OSError:
                    break
                time.sleep(interval)
        finally:
            with self.tcp_lock:
                if sock in self.tcp_clients:
                    self.tcp_clients.remove(sock)
            try:
                sock.close()
            except Exception:
                pass
            self.log_signal.emit(f"✘ TCP 断开: {addr[0]}:{addr[1]}")

    # ---- 串口模式 ----

    def _scan_ports(self):
        self.ser_port.clear()
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                self.ser_port.addItem(f"{p.device} - {p.description}")
        except ImportError:
            self.ser_port.addItem("COM1")
            self.ser_port.addItem("COM2")
        if sys.platform == "linux":
            import glob
            for p in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyS*")):
                self.ser_port.addItem(p)

    def toggle_serial(self):
        if self.running and self.serial_conn:
            self.stop_serial()
        else:
            self.start_serial()

    def start_serial(self):
        port_text = self.ser_port.currentText().split(" - ")[0].strip()
        baud = int(self.ser_baud.currentText())
        try:
            import serial
            self.serial_conn = serial.Serial(
                port=port_text, baudrate=baud,
                bytesize=8, parity="N", stopbits=1, timeout=0.5)
            self.running = True
            self.ser_btn.setText("■  断开")
            self.ser_status.setText(f"●  已连接 {port_text}")
            self.ser_status.setStyleSheet("color: #00ff88; font-weight: bold;")
            self.log_signal.emit(f"串口已连接: {port_text} @ {baud}")
            if self.ser_mode_modbus.isChecked():
                threading.Thread(target=self._serial_modbus_loop, daemon=True).start()
            else:
                threading.Thread(target=self._serial_continuous_loop, daemon=True).start()
        except Exception as e:
            self.log_signal.emit(f"串口连接失败: {e}")

    def stop_serial(self):
        self.running = False
        time.sleep(0.6)
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except Exception:
                pass
            self.serial_conn = None
        self.ser_btn.setText("▶  连接")
        self.ser_status.setText("●  未连接")
        self.ser_status.setStyleSheet("color: #666; font-weight: bold;")
        self.log_signal.emit("串口已断开")

    def _serial_modbus_loop(self):
        expected_slave = self.slave_spin.value()
        buffer = b""
        while self.running and self.serial_conn:
            try:
                data = self.serial_conn.read(256)
                if not data:
                    continue
                buffer += data
                while b"\r\n" in buffer:
                    frame, buffer = buffer.split(b"\r\n", 1)
                    text = frame.decode("ascii", errors="ignore").strip()
                    if not text.startswith(":"):
                        continue
                    try:
                        raw = bytes.fromhex(text[1:])
                    except ValueError:
                        continue
                    if len(raw) < 7:
                        continue
                    slave, func = raw[0], raw[1]
                    if slave != expected_slave:
                        continue
                    self.log_signal.emit(f"← 请求: {text}")
                    if func == 0x03:
                        w = self._get_weight()
                        hi, lo = self._weight_to_registers(w)
                        resp = self._build_modbus_response(slave, hi, lo)
                        self.serial_conn.write(resp)
                        self.log_signal.emit(f"→ 响应: {resp.decode('ascii').strip()} ({w:.1f}g)")
            except Exception as e:
                if self.running:
                    self.log_signal.emit(f"错误: {e}")
                break

    def _serial_continuous_loop(self):
        interval = self.ser_interval.value() / 1000.0
        while self.running and self.serial_conn:
            try:
                w = self._get_weight()
                line = f"{w:.1f}\r\n"
                self.serial_conn.write(line.encode("utf-8"))
                self.log_signal.emit(f"→ {line.strip()}")
                time.sleep(interval)
            except Exception as e:
                if self.running:
                    self.log_signal.emit(f"错误: {e}")
                break

    def _append_log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_text.append(f"[{ts}] {msg}")

    def closeEvent(self, event):
        self.running = False
        self.stop_tcp() if self.tcp_server else None
        self.stop_serial() if self.serial_conn else None
        event.accept()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="虚拟称重器")
    parser.add_argument("--ip", type=str, default="0.0.0.0", help="TCP 绑定 IP")
    parser.add_argument("--port", type=int, default=9001, help="TCP 端口")
    parser.add_argument("--name", type=str, default="", help="设备名称")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)
    w = WeightSimulator(default_ip=args.ip, default_port=args.port, name=args.name)
    w.show()
    sys.exit(app.exec_())
