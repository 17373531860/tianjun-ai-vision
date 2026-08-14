"""假 TCP 扫码器 (text_lon 协议模拟真枪).

- 24001: 设备口, 后端 ScannerService 连进来; 收 LON/LOFF 指令记灯态
- 24002: 控制口, 每连接一条命令:
    SCAN <code>   -> 向后端连接发 "<code>\r\n" (模拟扫到码)
    STATE         -> 返回 JSON {lamp, connected, events}
    CLEAR         -> 清事件日志
所有设备口事件带时间戳落 /tmp/fake_scanner_events.jsonl
"""
import json
import socket
import threading
import time

EVENTS = []
EVENTS_LOCK = threading.Lock()
STATE = {"lamp": None, "conn": None}  # conn: 当前后端 socket


def log_event(kind, detail=""):
    evt = {"t": time.time(), "kind": kind, "detail": detail,
           "ts": time.strftime("%H:%M:%S")}
    with EVENTS_LOCK:
        EVENTS.append(evt)
    with open("/tmp/fake_scanner_events.jsonl", "a") as f:
        f.write(json.dumps(evt, ensure_ascii=False) + "\n")
    print(f"[{evt['ts']}] {kind} {detail}", flush=True)


def device_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 24001))
    srv.listen(2)
    log_event("listen", "device port 24001")
    while True:
        conn, addr = srv.accept()
        log_event("backend_connected", str(addr))
        STATE["conn"] = conn
        threading.Thread(target=device_reader, args=(conn,), daemon=True).start()


def device_reader(conn):
    buf = b""
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "ignore").strip()
                if not text:
                    continue
                if text == "LON":
                    STATE["lamp"] = "ON"
                    log_event("LON", "lamp ON")
                elif text == "LOFF":
                    STATE["lamp"] = "OFF"
                    log_event("LOFF", "lamp OFF")
                else:
                    log_event("cmd", text)
    except OSError:
        pass
    log_event("backend_disconnected")
    if STATE.get("conn") is conn:
        STATE["conn"] = None


def control_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 24002))
    srv.listen(5)
    log_event("listen", "control port 24002")
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle_control, args=(c,), daemon=True).start()


def handle_control(c):
    try:
        c.settimeout(5)
        data = c.recv(1024).decode("utf-8", "ignore").strip()
        if data.upper().startswith("SCAN "):
            code = data[5:].strip()
            conn = STATE.get("conn")
            if conn is None:
                c.sendall(b'{"ok": false, "err": "backend not connected"}\n')
            else:
                conn.sendall((code + "\r\n").encode())
                log_event("scan_injected", code)
                c.sendall(b'{"ok": true}\n')
        elif data.upper() == "STATE":
            with EVENTS_LOCK:
                out = {"lamp": STATE["lamp"],
                       "connected": STATE.get("conn") is not None,
                       "events": EVENTS[-50:]}
            c.sendall((json.dumps(out, ensure_ascii=False) + "\n").encode())
        elif data.upper() == "CLEAR":
            with EVENTS_LOCK:
                EVENTS.clear()
            c.sendall(b'{"ok": true}\n')
        else:
            c.sendall(b'{"ok": false, "err": "unknown"}\n')
    except OSError:
        pass
    finally:
        c.close()


if __name__ == "__main__":
    open("/tmp/fake_scanner_events.jsonl", "w").close()
    threading.Thread(target=device_server, daemon=True).start()
    threading.Thread(target=control_server, daemon=True).start()
    while True:
        time.sleep(3600)
