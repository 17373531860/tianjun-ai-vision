"""
虚拟扫码器 — 模拟真实扫码器的 TCP 服务端

原理：真实扫码器在 IP:55256 上开放 TCP 服务端，
我们的软件作为客户端连上去，扫码器扫到码后发送 "条码\r\n"。

另外支持 `--wmax` 模式：额外监听 55266/55276/55286 三个端口，
让 tianjun 的 hotfix (text_lon → auto) 升级路径也能识别到设备。
CMD/IMG 端口仅保持 TCP 不断开即可让 WMaxDevice.connect() 成功，
RPT 端口负责在发送条码时以 RptCode(CmdType=200) 帧把条码推给
tianjun 的 rpt_recv_loop。
"""
import sys
import os
import struct
import socket
import threading
import time
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QSpinBox, QGroupBox,
    QListWidget, QSplitter, QCheckBox, QComboBox, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QIcon, QPalette, QColor


# ── WMax 三端口仿真 ──────────────────────────────────────────
# tianjun v2.7.7+ 的 hotfix 会把 device_type=text_lon 自动升级成 auto，
# 进而走 WMaxDeviceManager 的 55266/55276/55286 三端口协议。
# 本仿真只需：
#   1) 三个端口都能 accept TCP 且不主动关闭 → WMaxDevice.connect() 成功
#      → state.connected=True → UI 显示 "已连接"
#   2) 发条码时往 55286 RPT 客户端推一个 RptCode(CmdType=200) 帧，
#      payload ≥ 80 字节（tianjun._rpt_recv_loop 有长度过滤）。
# 不依赖 backend 模块，独立实现协议最小子集。

WMAX_CMD_PORT = 55266
WMAX_IMG_PORT = 55276
WMAX_RPT_PORT = 55286

_FLAG_HAS_DATA = 0x40000000
_FLAG_IS_PROTOBUF = 0x08000000
_DEFAULT_FLAG = _FLAG_IS_PROTOBUF | 0x01
_CMD_RPT_CODE = 200


def _pb_varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def _pb_tag(field_number: int, wire_type: int) -> bytes:
    return _pb_varint((field_number << 3) | wire_type)


def _pb_field_varint(fn: int, val: int) -> bytes:
    return _pb_tag(fn, 0) + _pb_varint(val)


def _pb_field_bytes(fn: int, val: bytes) -> bytes:
    return _pb_tag(fn, 2) + _pb_varint(len(val)) + val


def _pb_field_string(fn: int, val: str) -> bytes:
    return _pb_field_bytes(fn, val.encode("utf-8"))


def _pb_field_message(fn: int, msg: bytes) -> bytes:
    return _pb_field_bytes(fn, msg)


def _build_rpt_code_payload(code: str) -> bytes:
    """构造 RptCode 的 protobuf payload，兼容 tianjun decode_rpt_code。

    完整结构 (参考 backend/services/wmax/messages.py decode_rpt_code):
        {
          1: ts_msg{1=timestamp_ms},
          2: detail_msg{
              1=attempt_count,
              3=code_msg{1=code_str},
              4=result_msg{1=result_code},
              7=timing_msg{1=decode_time,2=total_time},
          },
        }
    再加一个填充字段让总长度 ≥ 80（tianjun 有 dlen<80 丢弃逻辑）。
    """
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFFFFFF
    ts_msg = _pb_field_varint(1, ts_ms)

    code_msg = _pb_field_string(1, code)
    result_msg = _pb_field_varint(1, 1)  # 1 = GoodRead
    timing_msg = _pb_field_varint(1, 3) + _pb_field_varint(2, 5)

    detail = (
        _pb_field_varint(1, 1)
        + _pb_field_message(3, code_msg)
        + _pb_field_message(4, result_msg)
        + _pb_field_message(7, timing_msg)
    )

    payload = (
        _pb_field_message(1, ts_msg)
        + _pb_field_message(2, detail)
    )

    # 填充（使用字段号 99 bytes，decoder 忽略未知字段）
    if len(payload) < 100:
        pad_len = 100 - len(payload)
        payload += _pb_field_bytes(99, b"\x00" * pad_len)

    return payload


def _pack_wmax_frame(cmd_type: int, cmd_index: int, payload: bytes) -> bytes:
    """按 WMax 二进制帧格式包装：0x5A 0x5A | Flag(4B) | CT | CI | DataLen(4B) | HeadChk | Payload | DataChk(2B) | 0xA5"""
    flag = _DEFAULT_FLAG | _FLAG_HAS_DATA
    buf = bytearray()
    buf += b"\x5A\x5A"
    buf += struct.pack(">I", flag)
    buf.append(cmd_type & 0xFF)
    buf.append(cmd_index & 0xFF)
    buf += struct.pack(">I", len(payload))
    head_chk = sum(buf[2:]) & 0xFF
    buf.append(head_chk)
    buf += payload
    data_chk = sum(payload) & 0xFFFF
    buf += struct.pack(">H", data_chk)
    buf.append(0xA5)
    return bytes(buf)


class WMaxSim:
    """三端口 WMax 仿真（CMD 55266 / IMG 55276 / RPT 55286）。"""

    def __init__(self, log_cb=None):
        self._log = log_cb or (lambda m: None)
        self._stop = threading.Event()
        self._servers: list[socket.socket] = []
        self._rpt_lock = threading.Lock()
        self._rpt_clients: list[socket.socket] = []
        self._cmd_idx = 0

    def start(self) -> list[int]:
        """依次起三个监听端口，返回成功启动的端口列表。"""
        started = []
        for port, handler, tag in [
            (WMAX_CMD_PORT, self._serve_cmd, "CMD"),
            (WMAX_IMG_PORT, self._serve_img, "IMG"),
            (WMAX_RPT_PORT, self._serve_rpt, "RPT"),
        ]:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                s.listen(8)
            except OSError as e:
                self._log(f"[WMax] {tag} {port} 绑定失败: {e}")
                continue
            self._servers.append(s)
            threading.Thread(
                target=self._accept_loop, args=(s, handler, tag),
                daemon=True, name=f"wmax-{tag.lower()}-{port}",
            ).start()
            started.append(port)
            self._log(f"[WMax] {tag} 监听 0.0.0.0:{port}")
        return started

    def stop(self):
        self._stop.set()
        for s in self._servers:
            try:
                s.close()
            except Exception:
                pass
        with self._rpt_lock:
            for c in self._rpt_clients:
                try:
                    c.close()
                except Exception:
                    pass
            self._rpt_clients.clear()

    def _accept_loop(self, server: socket.socket, handler, tag: str):
        while not self._stop.is_set():
            try:
                conn, addr = server.accept()
            except OSError:
                break
            conn.settimeout(60.0)
            threading.Thread(
                target=handler, args=(conn, addr),
                daemon=True, name=f"wmax-{tag.lower()}-cli",
            ).start()

    def _serve_cmd(self, conn: socket.socket, addr):
        """CMD 55266：收到任何 WMax 帧都回一个空响应帧，保持连接。"""
        self._log(f"[WMax CMD] + {addr[0]}:{addr[1]}")
        try:
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                except (socket.timeout, OSError):
                    continue
                if not data:
                    break
                # 每收到一包数据就吐一个 HandShake 响应帧让对方不 hang
                ack = _pack_wmax_frame(
                    cmd_type=3, cmd_index=0,
                    payload=b"\x00" * 8,
                )
                # 翻 IsResponse bit (简单重写 flag 字段)
                ack = bytearray(ack)
                flag = struct.unpack(">I", ack[2:6])[0] | 0x04000000
                ack[2:6] = struct.pack(">I", flag)
                # 重算 head_chk（数据长度没变,重算一下）
                head_end = 2 + 4 + 1 + 1 + 4  # 0x5A5A + flag + ct + ci + datalen
                ack[head_end] = sum(ack[2:head_end]) & 0xFF
                try:
                    conn.sendall(bytes(ack))
                except OSError:
                    break
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self._log(f"[WMax CMD] - {addr[0]}:{addr[1]}")

    def _serve_img(self, conn: socket.socket, addr):
        """IMG 55276：仅保持连接，不推图像。"""
        self._log(f"[WMax IMG] + {addr[0]}:{addr[1]}")
        try:
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                except (socket.timeout, OSError):
                    continue
                if not data:
                    break
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self._log(f"[WMax IMG] - {addr[0]}:{addr[1]}")

    def _serve_rpt(self, conn: socket.socket, addr):
        """RPT 55286：登记客户端，条码到来时推 RptCode 帧。"""
        self._log(f"[WMax RPT] + {addr[0]}:{addr[1]}")
        with self._rpt_lock:
            self._rpt_clients.append(conn)
        try:
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                except (socket.timeout, OSError):
                    continue
                if not data:
                    break
        finally:
            with self._rpt_lock:
                if conn in self._rpt_clients:
                    self._rpt_clients.remove(conn)
            try:
                conn.close()
            except Exception:
                pass
            self._log(f"[WMax RPT] - {addr[0]}:{addr[1]}")

    def push_code(self, code: str) -> int:
        """把条码以 RptCode 帧格式推给所有 RPT 客户端，返回成功条数。"""
        if not code:
            return 0
        self._cmd_idx = (self._cmd_idx + 1) & 0xFF
        payload = _build_rpt_code_payload(code)
        frame = _pack_wmax_frame(
            cmd_type=_CMD_RPT_CODE, cmd_index=self._cmd_idx,
            payload=payload,
        )
        sent = 0
        dead: list[socket.socket] = []
        with self._rpt_lock:
            clients = list(self._rpt_clients)
        for c in clients:
            try:
                c.sendall(frame)
                sent += 1
            except OSError:
                dead.append(c)
        if dead:
            with self._rpt_lock:
                for c in dead:
                    if c in self._rpt_clients:
                        self._rpt_clients.remove(c)
        return sent


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
    color: #00ff88; font-family: 'Consolas', 'Courier New', 'Microsoft YaHei', 'Noto Sans Mono CJK SC', 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', monospace;
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

    def __init__(self, default_port=55256, default_ip="0.0.0.0", name="",
                 enable_wmax: bool = False):
        super().__init__()
        self._default_port = default_port
        self._default_ip = default_ip
        self._name = name
        title = "虚拟扫码器"
        if name:
            title += f"  [ {name} ]"
        if enable_wmax:
            title += "  (WMax 三端口)"
        self.setWindowTitle(title)
        self.setMinimumSize(700, 560)

        self.server_socket = None
        self.clients = []
        self.lock = threading.Lock()
        self.running = False

        self.wmax_sim: "WMaxSim | None" = None
        self._enable_wmax = enable_wmax

        self._build_ui()
        self.log_signal.connect(self._append_log)
        self.client_changed.connect(self._refresh_clients)

        if self._enable_wmax:
            self.wmax_sim = WMaxSim(log_cb=lambda m: self.log_signal.emit(m))
            QTimer.singleShot(150, self._start_wmax_sim)

    def _start_wmax_sim(self):
        if not self.wmax_sim:
            return
        started = self.wmax_sim.start()
        if started:
            self.log_signal.emit(
                f"WMax 三端口仿真已启动: {', '.join(str(p) for p in started)}"
            )
        else:
            self.log_signal.emit("WMax 三端口仿真启动失败 (端口可能被占用)")

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
        _bf = QFont()
        _bf.setFamilies(["Consolas", "Microsoft YaHei", "Noto Sans Mono CJK SC",
                         "Noto Sans CJK SC", "WenQuanYi Micro Hei"])
        _bf.setPointSize(16)
        self.barcode_input.setFont(_bf)
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

        if self.wmax_sim:
            wmax_sent = self.wmax_sim.push_code(barcode)
            if wmax_sent:
                self.log_signal.emit(f"→ WMax RPT 推送: {barcode}  ({wmax_sent} 客户端)")

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
        if self.wmax_sim:
            self.wmax_sim.stop()
        event.accept()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="虚拟扫码器")
    parser.add_argument("--port", type=int, default=55256, help="监听端口")
    parser.add_argument("--name", type=str, default="", help="设备名称")
    parser.add_argument("--multi", type=int, default=0, help="同时启动多个实例")
    parser.add_argument("--ips", type=str, default="",
                        help="多实例绑定 IP，逗号分隔 (如 192.168.0.100,192.168.0.101)")
    parser.add_argument("--auto-start", action="store_true",
                        help="启动后自动开始监听（无需点按钮）")
    parser.add_argument("--wmax", action="store_true",
                        help="同时启动 WMax 三端口仿真 (55266/55276/55286)，"
                             "用于兼容 tianjun v2.7.7+ 的 text_lon→auto 升级")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)

    windows = []
    if args.multi >= 2:
        ips = [s.strip() for s in args.ips.split(",") if s.strip()] if args.ips else []
        for i in range(args.multi):
            name = f"扫码器 {chr(65 + i)}"
            # --wmax 只允许第一个实例启用 (三端口是单例)
            w = ScannerServer(default_port=args.port,
                              default_ip=ips[i] if i < len(ips) else "0.0.0.0",
                              name=name,
                              enable_wmax=args.wmax and i == 0)
            w.move(80 + i * 720, 80)
            w.show()
            windows.append(w)
    else:
        w = ScannerServer(default_port=args.port, name=args.name,
                          enable_wmax=args.wmax)
        w.show()
        windows.append(w)

    if args.auto_start:
        from PyQt5.QtCore import QTimer
        for w in windows:
            QTimer.singleShot(200, w.start_server)

    sys.exit(app.exec_())
