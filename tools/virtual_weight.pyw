"""
虚拟称重器 — 模拟真实称重器的 TCP / 串口通信
使用 tkinter（Python 自带），无需额外安装任何库
串口模式需要 pyserial（软件自带）
"""
import socket
import struct
import threading
import time
import random
import tkinter as tk
from tkinter import ttk
from datetime import datetime


class WeightSimulator:
    def __init__(self, root, bind_ip="0.0.0.0", port=9001, name="称重器"):
        self.root = root
        self.name = name
        self.server_socket = None
        self.tcp_clients = []
        self.tcp_lock = threading.Lock()
        self.serial_conn = None
        self.running_tcp = False
        self.running_serial = False

        root.title(f"虚拟称重器 — {name}")
        root.geometry("680x620")
        root.configure(bg="#1a1a2e")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TFrame", background="#1a1a2e")
        style.configure("Dark.TLabel", background="#1a1a2e", foreground="#b0b0b0", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background="#1a1a2e", foreground="#ffaa00", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Dark.TLabelframe", background="#16213e", foreground="#00d2ff", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Dark.TLabelframe.Label", background="#16213e", foreground="#00d2ff")
        style.configure("Dark.TNotebook", background="#1a1a2e")
        style.configure("Dark.TNotebook.Tab", background="#16213e", foreground="#888888",
                         font=("Microsoft YaHei UI", 10, "bold"), padding=[12, 6])
        style.map("Dark.TNotebook.Tab", background=[("selected", "#0f3460")], foreground=[("selected", "#00d2ff")])

        main = ttk.Frame(root, style="Dark.TFrame")
        main.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)

        ttk.Label(main, text=f"⚖  虚拟称重器  —  {name}", style="Title.TLabel").pack(pady=(0, 8))

        # 重量
        wg = ttk.LabelFrame(main, text="重量模拟", style="Dark.TLabelframe")
        wg.pack(fill=tk.X, pady=(0, 6))

        disp_frame = ttk.Frame(wg, style="Dark.TFrame")
        disp_frame.pack(fill=tk.X, padx=10, pady=6)

        self.weight_var = tk.DoubleVar(value=0.0)
        self.weight_display = tk.Label(disp_frame, text="0.0", bg="#1a1a2e", fg="#ffaa00",
                                        font=("Consolas", 38, "bold"))
        self.weight_display.pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(disp_frame, text="g", bg="#1a1a2e", fg="#888888", font=("Consolas", 18)).pack(side=tk.LEFT, pady=(14, 0))

        spin_frame = ttk.Frame(disp_frame, style="Dark.TFrame")
        spin_frame.pack(side=tk.RIGHT)
        tk.Label(spin_frame, text="精确输入", bg="#1a1a2e", fg="#b0b0b0", font=("Microsoft YaHei UI", 9)).pack()
        self.weight_entry = tk.Entry(spin_frame, textvariable=self.weight_var, width=12, bg="#0f3460", fg="#ffffff",
                                      insertbackground="#00d2ff", font=("Consolas", 14), relief=tk.FLAT, bd=2, justify=tk.CENTER)
        self.weight_entry.pack()
        self.weight_var.trace_add("write", self._on_weight_change)

        # 滑块
        self.slider = tk.Scale(wg, from_=0, to=50000, resolution=1, orient=tk.HORIZONTAL,
                                bg="#16213e", fg="#ffaa00", troughcolor="#0f3460", highlightbackground="#16213e",
                                font=("Consolas", 9), showvalue=False, length=600,
                                command=lambda v: self.weight_var.set(float(v)))
        self.slider.pack(fill=tk.X, padx=10)

        # 快捷按钮
        preset_frame = ttk.Frame(wg, style="Dark.TFrame")
        preset_frame.pack(fill=tk.X, padx=10, pady=(4, 6))
        tk.Label(preset_frame, text="快捷", bg="#1a1a2e", fg="#b0b0b0", font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=(0, 6))
        for val in [0, 100, 500, 1000, 2500, 5000, 10000, 15000, 25000]:
            lbl = f"{val/1000:.1f}kg" if val >= 1000 else f"{val}g"
            tk.Button(preset_frame, text=lbl, command=lambda v=val: self.weight_var.set(v),
                       bg="#0f3460", fg="#00d2ff", relief=tk.FLAT, font=("Consolas", 9),
                       cursor="hand2", padx=4).pack(side=tk.LEFT, padx=2)

        # 抖动
        jitter_frame = ttk.Frame(wg, style="Dark.TFrame")
        jitter_frame.pack(fill=tk.X, padx=10, pady=(0, 6))
        self.jitter_on = tk.BooleanVar(value=False)
        tk.Checkbutton(jitter_frame, text="随机波动 ±", variable=self.jitter_on,
                        bg="#1a1a2e", fg="#b0b0b0", selectcolor="#0f3460", activebackground="#1a1a2e",
                        font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)
        self.jitter_val = tk.DoubleVar(value=0.5)
        tk.Entry(jitter_frame, textvariable=self.jitter_val, width=6, bg="#0f3460", fg="#ffffff",
                  font=("Consolas", 10), relief=tk.FLAT, bd=2).pack(side=tk.LEFT, padx=2)
        tk.Label(jitter_frame, text="g", bg="#1a1a2e", fg="#b0b0b0", font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)

        # Tabs
        nb = ttk.Notebook(main, style="Dark.TNotebook")
        nb.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        # TCP tab
        tcp_tab = ttk.Frame(nb, style="Dark.TFrame")
        nb.add(tcp_tab, text="  TCP 模式  ")

        tcp_row = ttk.Frame(tcp_tab, style="Dark.TFrame")
        tcp_row.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(tcp_row, text="绑定 IP", style="Dark.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        self.tcp_ip_var = tk.StringVar(value=bind_ip)
        tk.Entry(tcp_row, textvariable=self.tcp_ip_var, width=14, bg="#0f3460", fg="#ffffff",
                  font=("Consolas", 11), relief=tk.FLAT, bd=2).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(tcp_row, text="端口", style="Dark.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        self.tcp_port_var = tk.StringVar(value=str(port))
        tk.Entry(tcp_row, textvariable=self.tcp_port_var, width=7, bg="#0f3460", fg="#ffffff",
                  font=("Consolas", 11), relief=tk.FLAT, bd=2).pack(side=tk.LEFT, padx=(0, 8))
        self.tcp_btn = tk.Button(tcp_row, text="▶ 启动", command=self.toggle_tcp,
                                  bg="#1a5276", fg="#e0e0e0", font=("Microsoft YaHei UI", 10, "bold"),
                                  relief=tk.FLAT, width=10, cursor="hand2")
        self.tcp_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.tcp_status_var = tk.StringVar(value="●  未启动")
        self.tcp_status = tk.Label(tcp_row, textvariable=self.tcp_status_var, bg="#1a1a2e", fg="#666666",
                                    font=("Microsoft YaHei UI", 11, "bold"))
        self.tcp_status.pack(side=tk.LEFT)

        tcp_opt = ttk.Frame(tcp_tab, style="Dark.TFrame")
        tcp_opt.pack(fill=tk.X, padx=10, pady=(0, 6))
        ttk.Label(tcp_opt, text="发送间隔", style="Dark.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        self.tcp_interval_var = tk.StringVar(value="500")
        tk.Entry(tcp_opt, textvariable=self.tcp_interval_var, width=6, bg="#0f3460", fg="#ffffff",
                  font=("Consolas", 10), relief=tk.FLAT, bd=2).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Label(tcp_opt, text="ms", style="Dark.TLabel").pack(side=tk.LEFT, padx=(0, 14))

        ttk.Label(tcp_opt, text="格式", style="Dark.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        self.tcp_fmt_var = tk.StringVar(value="纯数值")
        fmt_menu = tk.OptionMenu(tcp_opt, self.tcp_fmt_var, "纯数值", "带单位 (g)", "CSV")
        fmt_menu.config(bg="#0f3460", fg="#ffffff", font=("Microsoft YaHei UI", 9), relief=tk.FLAT, highlightthickness=0)
        fmt_menu.pack(side=tk.LEFT)

        # 串口 tab
        ser_tab = ttk.Frame(nb, style="Dark.TFrame")
        nb.add(ser_tab, text="  串口模式  ")

        ser_row = ttk.Frame(ser_tab, style="Dark.TFrame")
        ser_row.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(ser_row, text="串口", style="Dark.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        self.ser_port_var = tk.StringVar()
        self.ser_combo = tk.Entry(ser_row, textvariable=self.ser_port_var, width=16, bg="#0f3460", fg="#ffffff",
                                   font=("Consolas", 10), relief=tk.FLAT, bd=2)
        self.ser_combo.pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(ser_row, text="刷新", command=self._scan_ports, bg="#0f3460", fg="#00d2ff",
                   font=("Microsoft YaHei UI", 9), relief=tk.FLAT, cursor="hand2").pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(ser_row, text="波特率", style="Dark.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        self.ser_baud_var = tk.StringVar(value="9600")
        tk.OptionMenu(ser_row, self.ser_baud_var, "9600", "19200", "38400", "115200").pack(side=tk.LEFT, padx=(0, 8))

        self.ser_btn = tk.Button(ser_row, text="▶ 连接", command=self.toggle_serial,
                                  bg="#1a5276", fg="#e0e0e0", font=("Microsoft YaHei UI", 10, "bold"),
                                  relief=tk.FLAT, width=10, cursor="hand2")
        self.ser_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.ser_status_var = tk.StringVar(value="●  未连接")
        self.ser_status = tk.Label(ser_row, textvariable=self.ser_status_var, bg="#1a1a2e", fg="#666666",
                                    font=("Microsoft YaHei UI", 11, "bold"))
        self.ser_status.pack(side=tk.LEFT)

        ser_opt = ttk.Frame(ser_tab, style="Dark.TFrame")
        ser_opt.pack(fill=tk.X, padx=10, pady=(0, 6))
        self.ser_mode_var = tk.StringVar(value="modbus")
        tk.Radiobutton(ser_opt, text="Modbus ASCII 从机", variable=self.ser_mode_var, value="modbus",
                        bg="#1a1a2e", fg="#b0b0b0", selectcolor="#0f3460", activebackground="#1a1a2e",
                        font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT, padx=(0, 12))
        tk.Radiobutton(ser_opt, text="连续发送", variable=self.ser_mode_var, value="continuous",
                        bg="#1a1a2e", fg="#b0b0b0", selectcolor="#0f3460", activebackground="#1a1a2e",
                        font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(ser_opt, text="从站ID", style="Dark.TLabel").pack(side=tk.LEFT, padx=(8, 4))
        self.slave_var = tk.StringVar(value="1")
        tk.Entry(ser_opt, textvariable=self.slave_var, width=4, bg="#0f3460", fg="#ffffff",
                  font=("Consolas", 10), relief=tk.FLAT, bd=2).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(ser_opt, text="间隔", style="Dark.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        self.ser_interval_var = tk.StringVar(value="500")
        tk.Entry(ser_opt, textvariable=self.ser_interval_var, width=6, bg="#0f3460", fg="#ffffff",
                  font=("Consolas", 10), relief=tk.FLAT, bd=2).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Label(ser_opt, text="ms", style="Dark.TLabel").pack(side=tk.LEFT)

        self._scan_ports()

        # 日志
        log_frame = ttk.LabelFrame(main, text="通信日志", style="Dark.TLabelframe")
        log_frame.pack(fill=tk.X)
        self.log_text = tk.Text(log_frame, bg="#0a0a1a", fg="#00ff88", height=7,
                                 font=("Consolas", 10), relief=tk.FLAT, bd=2, state=tk.DISABLED)
        self.log_text.pack(fill=tk.X, padx=6, pady=6)

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        def _do():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, _do)

    def _on_weight_change(self, *_):
        try:
            v = self.weight_var.get()
            self.weight_display.config(text=f"{v:.1f}")
            self.slider.set(int(v))
        except tk.TclError:
            pass

    def _get_weight(self):
        try:
            w = self.weight_var.get()
        except tk.TclError:
            w = 0.0
        if self.jitter_on.get():
            try:
                j = self.jitter_val.get()
            except tk.TclError:
                j = 0.5
            w += random.uniform(-j, j)
        return round(w, 1)

    def _format_weight_tcp(self, w):
        fmt = self.tcp_fmt_var.get()
        if "CSV" in fmt:
            return f"weight,{w:.1f}"
        elif "g" in fmt:
            return f"{w:.1f}g"
        return f"{w:.1f}"

    @staticmethod
    def _lrc(data):
        return (-sum(data)) & 0xFF

    def _weight_to_registers(self, weight):
        raw = int(weight * 10)
        if raw < 0:
            raw += 0x100000000
        return (raw >> 16) & 0xFFFF, raw & 0xFFFF

    def _build_modbus_response(self, slave, hi, lo):
        pdu = bytes([slave, 0x03, 4,
                     (hi >> 8) & 0xFF, hi & 0xFF,
                     (lo >> 8) & 0xFF, lo & 0xFF])
        lrc = self._lrc(pdu)
        return f":{pdu.hex().upper()}{lrc:02X}\r\n".encode("ascii")

    def _scan_ports(self):
        ports = []
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                ports.append(p.device)
        except ImportError:
            ports = ["COM1", "COM2"]
        import sys as _sys
        if _sys.platform == "linux":
            import glob as _glob
            for p in sorted(_glob.glob("/dev/ttyUSB*") + _glob.glob("/dev/ttyS*")):
                if p not in ports:
                    ports.append(p)
        if ports:
            self.ser_port_var.set(ports[0])

    # TCP
    def toggle_tcp(self):
        if self.running_tcp:
            self.stop_tcp()
        else:
            self.start_tcp()

    def start_tcp(self):
        ip = self.tcp_ip_var.get().strip() or "0.0.0.0"
        port = int(self.tcp_port_var.get().strip() or "9001")
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((ip, port))
            self.server_socket.listen(5)
            self.server_socket.settimeout(1.0)
            self.running_tcp = True
            self.tcp_btn.config(text="■ 停止", bg="#8b0000")
            self.tcp_status_var.set(f"●  监听中  {ip}:{port}")
            self.tcp_status.config(fg="#00ff88")
            self.log(f"TCP 启动: {ip}:{port}")
            threading.Thread(target=self._tcp_accept, daemon=True).start()
        except OSError as e:
            self.log(f"TCP 启动失败: {e}")
            self.tcp_status_var.set("●  启动失败")
            self.tcp_status.config(fg="#ff4444")

    def stop_tcp(self):
        self.running_tcp = False
        with self.tcp_lock:
            for c in self.tcp_clients:
                try: c.close()
                except: pass
            self.tcp_clients.clear()
        if self.server_socket:
            try: self.server_socket.close()
            except: pass
            self.server_socket = None
        self.tcp_btn.config(text="▶ 启动", bg="#1a5276")
        self.tcp_status_var.set("●  已停止")
        self.tcp_status.config(fg="#666666")
        self.log("TCP 已停止")

    def _tcp_accept(self):
        while self.running_tcp and self.server_socket:
            try:
                cs, addr = self.server_socket.accept()
                with self.tcp_lock:
                    self.tcp_clients.append(cs)
                self.log(f"✔ TCP 客户端: {addr[0]}:{addr[1]}")
                threading.Thread(target=self._tcp_handler, args=(cs, addr), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _tcp_handler(self, sock, addr):
        try:
            interval_ms = int(self.tcp_interval_var.get() or "500")
        except ValueError:
            interval_ms = 500
        interval = interval_ms / 1000.0
        try:
            while self.running_tcp:
                w = self._get_weight()
                line = self._format_weight_tcp(w) + "\r\n"
                try:
                    sock.sendall(line.encode("utf-8"))
                    self.log(f"→ [{addr[0]}] {line.strip()}")
                except OSError:
                    break
                time.sleep(interval)
        finally:
            with self.tcp_lock:
                if sock in self.tcp_clients:
                    self.tcp_clients.remove(sock)
            try: sock.close()
            except: pass
            self.log(f"✘ TCP 断开: {addr[0]}:{addr[1]}")

    # 串口
    def toggle_serial(self):
        if self.running_serial:
            self.stop_serial()
        else:
            self.start_serial()

    def start_serial(self):
        port_text = self.ser_port_var.get().strip()
        baud = int(self.ser_baud_var.get())
        try:
            import serial
            self.serial_conn = serial.Serial(port=port_text, baudrate=baud,
                                              bytesize=8, parity="N", stopbits=1, timeout=0.5)
            self.running_serial = True
            self.ser_btn.config(text="■ 断开", bg="#8b0000")
            self.ser_status_var.set(f"●  已连接 {port_text}")
            self.ser_status.config(fg="#00ff88")
            self.log(f"串口连接: {port_text} @ {baud}")
            if self.ser_mode_var.get() == "modbus":
                threading.Thread(target=self._serial_modbus, daemon=True).start()
            else:
                threading.Thread(target=self._serial_continuous, daemon=True).start()
        except Exception as e:
            self.log(f"串口连接失败: {e}")
            self.ser_status_var.set("●  连接失败")
            self.ser_status.config(fg="#ff4444")

    def stop_serial(self):
        self.running_serial = False
        time.sleep(0.6)
        if self.serial_conn:
            try: self.serial_conn.close()
            except: pass
            self.serial_conn = None
        self.ser_btn.config(text="▶ 连接", bg="#1a5276")
        self.ser_status_var.set("●  未连接")
        self.ser_status.config(fg="#666666")
        self.log("串口已断开")

    def _serial_modbus(self):
        try:
            expected_slave = int(self.slave_var.get())
        except ValueError:
            expected_slave = 1
        buffer = b""
        while self.running_serial and self.serial_conn:
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
                    self.log(f"← 请求: {text}")
                    if func == 0x03:
                        w = self._get_weight()
                        hi, lo = self._weight_to_registers(w)
                        resp = self._build_modbus_response(slave, hi, lo)
                        self.serial_conn.write(resp)
                        self.log(f"→ 响应: {resp.decode('ascii').strip()} ({w:.1f}g)")
            except Exception as e:
                if self.running_serial:
                    self.log(f"错误: {e}")
                break

    def _serial_continuous(self):
        try:
            interval_ms = int(self.ser_interval_var.get() or "500")
        except ValueError:
            interval_ms = 500
        interval = interval_ms / 1000.0
        while self.running_serial and self.serial_conn:
            try:
                w = self._get_weight()
                line = f"{w:.1f}\r\n"
                self.serial_conn.write(line.encode("utf-8"))
                self.log(f"→ {line.strip()}")
                time.sleep(interval)
            except Exception as e:
                if self.running_serial:
                    self.log(f"错误: {e}")
                break

    def _on_close(self):
        if self.running_tcp:
            self.stop_tcp()
        if self.running_serial:
            self.stop_serial()
        self.root.destroy()


if __name__ == "__main__":
    import sys
    port = 9001
    name = "称重器"
    ip = "0.0.0.0"
    for i, a in enumerate(sys.argv[1:]):
        if a == "--port" and i + 2 <= len(sys.argv): port = int(sys.argv[i + 2])
        if a == "--name" and i + 2 <= len(sys.argv): name = sys.argv[i + 2]
        if a == "--ip" and i + 2 <= len(sys.argv): ip = sys.argv[i + 2]

    root = tk.Tk()
    WeightSimulator(root, bind_ip=ip, port=port, name=name)
    root.mainloop()
