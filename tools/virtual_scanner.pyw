"""
虚拟扫码器 — 模拟真实扫码器的 TCP 服务端
使用 tkinter（Python 自带），无需额外安装任何库
"""
import socket
import threading
import tkinter as tk
from tkinter import ttk
from datetime import datetime


class ScannerServer:
    def __init__(self, root, bind_ip="0.0.0.0", port=55256, name="扫码器"):
        self.root = root
        self.bind_ip = bind_ip
        self.port = port
        self.name = name
        self.server_socket = None
        self.clients = []
        self.lock = threading.Lock()
        self.running = False
        self.counter = 1

        root.title(f"虚拟扫码器 — {name}")
        root.geometry("720x580")
        root.configure(bg="#1a1a2e")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TFrame", background="#1a1a2e")
        style.configure("Dark.TLabel", background="#1a1a2e", foreground="#b0b0b0", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background="#1a1a2e", foreground="#00d2ff", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Status.TLabel", background="#1a1a2e", foreground="#666666", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Dark.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Send.TButton", font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Dark.TLabelframe", background="#16213e", foreground="#00d2ff", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Dark.TLabelframe.Label", background="#16213e", foreground="#00d2ff")
        style.configure("Dark.TCheckbutton", background="#1a1a2e", foreground="#b0b0b0", font=("Microsoft YaHei UI", 10))
        style.configure("Dark.TEntry", fieldbackground="#0f3460", foreground="#ffffff", font=("Consolas", 12))

        main = ttk.Frame(root, style="Dark.TFrame")
        main.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)

        ttk.Label(main, text=f"📡  虚拟扫码器  —  {name}", style="Title.TLabel").pack(pady=(0, 8))

        # 服务配置
        srv = ttk.LabelFrame(main, text="服务配置", style="Dark.TLabelframe")
        srv.pack(fill=tk.X, pady=(0, 6))
        srv_row = ttk.Frame(srv, style="Dark.TFrame")
        srv_row.pack(fill=tk.X, padx=8, pady=6)

        ttk.Label(srv_row, text="绑定 IP", style="Dark.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        self.ip_var = tk.StringVar(value=bind_ip)
        self.ip_entry = tk.Entry(srv_row, textvariable=self.ip_var, width=16, bg="#0f3460", fg="#ffffff",
                                  insertbackground="#00d2ff", font=("Consolas", 11), relief=tk.FLAT, bd=2)
        self.ip_entry.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(srv_row, text="端口", style="Dark.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        self.port_var = tk.StringVar(value=str(port))
        self.port_entry = tk.Entry(srv_row, textvariable=self.port_var, width=7, bg="#0f3460", fg="#ffffff",
                                    insertbackground="#00d2ff", font=("Consolas", 11), relief=tk.FLAT, bd=2)
        self.port_entry.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_start = tk.Button(srv_row, text="▶ 启动", command=self.toggle_server,
                                    bg="#1a5276", fg="#e0e0e0", activebackground="#0f3460",
                                    font=("Microsoft YaHei UI", 10, "bold"), relief=tk.FLAT, width=10, cursor="hand2")
        self.btn_start.pack(side=tk.LEFT, padx=(0, 10))

        self.status_var = tk.StringVar(value="●  未启动")
        self.status_label = tk.Label(srv_row, textvariable=self.status_var, bg="#1a1a2e", fg="#666666",
                                      font=("Microsoft YaHei UI", 11, "bold"))
        self.status_label.pack(side=tk.LEFT)

        # 中间区
        mid = ttk.Frame(main, style="Dark.TFrame")
        mid.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        # 左：发送面板
        left = ttk.LabelFrame(mid, text="发送条码", style="Dark.TLabelframe")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        self.barcode_var = tk.StringVar(value="SN-0001")
        self.barcode_entry = tk.Entry(left, textvariable=self.barcode_var, bg="#0f3460", fg="#00ff88",
                                       insertbackground="#00d2ff", font=("Consolas", 18), relief=tk.FLAT, bd=3)
        self.barcode_entry.pack(fill=tk.X, padx=10, pady=(10, 6))
        self.barcode_entry.bind("<Return>", lambda e: self.send_barcode())

        self.btn_send = tk.Button(left, text="发  送", command=self.send_barcode,
                                   bg="#00b4d8", fg="#000000", activebackground="#0099cc",
                                   font=("Microsoft YaHei UI", 14, "bold"), relief=tk.FLAT, height=2, cursor="hand2")
        self.btn_send.pack(fill=tk.X, padx=10, pady=(0, 8))

        opt_frame = ttk.Frame(left, style="Dark.TFrame")
        opt_frame.pack(fill=tk.X, padx=10)

        self.auto_inc = tk.BooleanVar(value=True)
        tk.Checkbutton(opt_frame, text="自动递增", variable=self.auto_inc,
                        bg="#1a1a2e", fg="#b0b0b0", selectcolor="#0f3460", activebackground="#1a1a2e",
                        font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)

        tk.Label(opt_frame, text="  前缀", bg="#1a1a2e", fg="#b0b0b0",
                  font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)
        self.prefix_var = tk.StringVar(value="SN-")
        tk.Entry(opt_frame, textvariable=self.prefix_var, width=8, bg="#0f3460", fg="#ffffff",
                  font=("Consolas", 10), relief=tk.FLAT, bd=2).pack(side=tk.LEFT, padx=2)

        # 预设
        preset_frame = ttk.Frame(left, style="Dark.TFrame")
        preset_frame.pack(fill=tk.X, padx=10, pady=(6, 4))
        tk.Label(preset_frame, text="预设", bg="#1a1a2e", fg="#b0b0b0",
                  font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT, padx=(0, 6))
        presets = ["BOX-001", "BOX-002", "BOX-003", "PART-A-001", "PART-B-001"]
        for p in presets:
            tk.Button(preset_frame, text=p, command=lambda t=p: self.barcode_var.set(t),
                       bg="#0f3460", fg="#00d2ff", relief=tk.FLAT, font=("Consolas", 9),
                       cursor="hand2", padx=6).pack(side=tk.LEFT, padx=2)

        # 右：客户端
        right = ttk.LabelFrame(mid, text="已连接客户端", style="Dark.TLabelframe")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(0, 0))
        right.configure(width=200)

        self.client_listbox = tk.Listbox(right, bg="#0f3460", fg="#00d2ff", selectbackground="#1a5276",
                                          font=("Consolas", 10), relief=tk.FLAT, width=22, height=6)
        self.client_listbox.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 2))
        self.client_count_var = tk.StringVar(value="0 个连接")
        tk.Label(right, textvariable=self.client_count_var, bg="#16213e", fg="#666666",
                  font=("Microsoft YaHei UI", 9)).pack(pady=(0, 6))

        # 日志
        log_frame = ttk.LabelFrame(main, text="通信日志", style="Dark.TLabelframe")
        log_frame.pack(fill=tk.X)
        self.log_text = tk.Text(log_frame, bg="#0a0a1a", fg="#00ff88", height=7,
                                 font=("Consolas", 10), relief=tk.FLAT, bd=2, state=tk.DISABLED,
                                 insertbackground="#00d2ff")
        self.log_text.pack(fill=tk.X, padx=6, pady=6)

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        def _do():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, _do)

    def toggle_server(self):
        if self.running:
            self.stop_server()
        else:
            self.start_server()

    def start_server(self):
        ip = self.ip_var.get().strip() or "0.0.0.0"
        port = int(self.port_var.get().strip() or "55256")
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((ip, port))
            self.server_socket.listen(5)
            self.server_socket.settimeout(1.0)
            self.running = True
            self.btn_start.config(text="■ 停止", bg="#8b0000")
            self.ip_entry.config(state=tk.DISABLED)
            self.port_entry.config(state=tk.DISABLED)
            self.status_var.set(f"●  监听中  {ip}:{port}")
            self.status_label.config(fg="#00ff88")
            self.log(f"服务器启动，监听 {ip}:{port}")
            threading.Thread(target=self._accept_loop, daemon=True).start()
        except OSError as e:
            self.log(f"启动失败: {e}")
            self.status_var.set("●  启动失败")
            self.status_label.config(fg="#ff4444")

    def stop_server(self):
        self.running = False
        with self.lock:
            for c in self.clients:
                try: c["socket"].close()
                except: pass
            self.clients.clear()
        if self.server_socket:
            try: self.server_socket.close()
            except: pass
        self.btn_start.config(text="▶ 启动", bg="#1a5276")
        self.ip_entry.config(state=tk.NORMAL)
        self.port_entry.config(state=tk.NORMAL)
        self.status_var.set("●  已停止")
        self.status_label.config(fg="#666666")
        self._refresh_clients()
        self.log("服务器已停止")

    def _accept_loop(self):
        while self.running:
            try:
                cs, addr = self.server_socket.accept()
                with self.lock:
                    self.clients.append({"socket": cs, "addr": f"{addr[0]}:{addr[1]}"})
                self.log(f"✔ 客户端连接: {addr[0]}:{addr[1]}")
                self._refresh_clients()
                threading.Thread(target=self._reader, args=(cs, addr), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _reader(self, sock, addr):
        sock.settimeout(2.0)
        try:
            while self.running:
                try:
                    data = sock.recv(1024)
                    if not data: break
                    text = data.decode("utf-8", errors="ignore").strip()
                    if text:
                        self.log(f"← [{addr[0]}:{addr[1]}] {text}")
                except socket.timeout: continue
                except OSError: break
        finally:
            with self.lock:
                self.clients = [c for c in self.clients if c["socket"] != sock]
            try: sock.close()
            except: pass
            self.log(f"✘ 客户端断开: {addr[0]}:{addr[1]}")
            self._refresh_clients()

    def send_barcode(self):
        barcode = self.barcode_var.get().strip()
        if not barcode: return
        data = (barcode + "\r\n").encode("utf-8")
        sent = 0
        with self.lock:
            dead = []
            for c in self.clients:
                try:
                    c["socket"].sendall(data)
                    sent += 1
                except OSError:
                    dead.append(c)
            for d in dead:
                self.clients.remove(d)
        if dead: self._refresh_clients()
        self.log(f"→ 发送: {barcode}  ({sent} 客户端)" if sent else f"→ 发送: {barcode}  (无客户端)")
        if self.auto_inc.get():
            self.counter += 1
            self.barcode_var.set(f"{self.prefix_var.get()}{self.counter:04d}")

    def _refresh_clients(self):
        def _do():
            self.client_listbox.delete(0, tk.END)
            with self.lock:
                for c in self.clients:
                    self.client_listbox.insert(tk.END, f"  {c['addr']}")
                n = len(self.clients)
            self.client_count_var.set(f"{n} 个连接")
        self.root.after(0, _do)

    def _on_close(self):
        self.stop_server()
        self.root.destroy()


if __name__ == "__main__":
    import sys
    port = 55256
    name = "扫码器"
    ip = "0.0.0.0"
    for i, a in enumerate(sys.argv[1:]):
        if a == "--port" and i + 2 <= len(sys.argv): port = int(sys.argv[i + 2])
        if a == "--name" and i + 2 <= len(sys.argv): name = sys.argv[i + 2]
        if a == "--ip" and i + 2 <= len(sys.argv): ip = sys.argv[i + 2]

    root = tk.Tk()
    ScannerServer(root, bind_ip=ip, port=port, name=name)
    root.mainloop()
