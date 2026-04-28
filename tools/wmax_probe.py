#!/usr/bin/env python3
"""WMax 扫码器 TCP 探针 — 独立测试脚本

跟项目代码无关,只用 Python 标准库,用于:
  1) 直连 WMax 扫码器某端口(默认 55256, 老 LON/LOFF 文本端口)
  2) 手动发 LON / LOFF,看设备实际反应
  3) 实时打印设备推回来的字节(ASCII 优先,二进制走 hex)

针对你提到的两个坑:
  - 代理劫持(Mihomo / Clash TUN 模式):脚本默认 SO_BINDTODEVICE 把 socket
    强绑到指定网卡,绕过 TUN 路由;退化路径用 bind(local_ip) 走系统选路。
  - 设备冷却拒绝(连接 3-5 秒后被踢):MSG_PEEK 探活,被踢就指数退避重连。

用法示例
--------
连扫码器 192.168.0.100:55256(默认 LON 端口),从有线网卡 enp3s0 直出:
  sudo python3 tools/wmax_probe.py --ip 192.168.0.100 --iface enp3s0

不用 sudo,但要确保你的 192.168.0.x 路由不走 Clash:
  python3 tools/wmax_probe.py --ip 192.168.0.100 --bind 192.168.0.5

跑起来后在终端输入:
  lon       发 b"LON\\r\\n"
  loff      发 b"LOFF\\r\\n"
  raw xxx   原样发 xxx 后接 \\r\\n
  hex AABB  发原始字节(十六进制)
  port 55266  断开重连到 55266 端口
  q / quit / Ctrl+C  退出(自动 LOFF)

注意
----
  现有后端的 v2.7.7c 注释写过:WMax 真机 55256 不响应 ASCII LON,这个脚本就是
  让你**亲自验证**这个结论。如果真发了 LON 设备能扫,那就是后端那条路径过早放弃。
"""

from __future__ import annotations

import argparse
import errno
import os
import platform
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Optional


# ─────────────────────────── 工具函数 ───────────────────────────

def log(tag: str, msg: str) -> None:
    ts = time.strftime("%H:%M:%S") + f".{int((time.time() % 1) * 1000):03d}"
    sys.stdout.write(f"[{ts}] {tag} {msg}\n")
    sys.stdout.flush()


def diagnose_route(target_ip: str) -> None:
    """打印一下系统对目标 IP 的路由,帮你确认会不会走 Mihomo。"""
    try:
        out = subprocess.check_output(
            ["ip", "route", "get", target_ip],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        log("[ROUTE]", out)
        if "Mihomo" in out or "utun" in out or "tun0" in out:
            log("[!]", "目标 IP 当前会走代理 TUN!请用 --iface 或 --bind,"
                       "或先在 Clash/Mihomo 里把局域网设为 DIRECT")
    except Exception as e:
        log("[ROUTE]", f"诊断失败: {e}")


def diagnose_local_ifaces() -> None:
    """打印本机网卡 IPv4,方便你挑 --bind。"""
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        log("[IFACE]", "本机 IPv4 地址:")
        for line in out.splitlines():
            sys.stdout.write("           " + line + "\n")
        sys.stdout.flush()
    except Exception as e:
        log("[IFACE]", f"诊断失败: {e}")


# ─────────────────────────── socket 工厂 ───────────────────────────

SO_BINDTODEVICE = 25  # Linux 专用


def make_socket(*, iface: Optional[str], bind_ip: Optional[str],
                connect_timeout: float) -> socket.socket:
    """按优先级处理代理劫持:
        1. iface (Linux SO_BINDTODEVICE):最强,需要 root
        2. bind_ip:走源 IP 选路由,无需 root
        3. 都不给:听天由命(可能被 Clash 劫持)
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    if hasattr(socket, "TCP_KEEPIDLE"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)

    if iface:
        if platform.system() != "Linux":
            raise RuntimeError("--iface 只在 Linux 可用")
        try:
            sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, iface.encode())
            log("[BIND]", f"SO_BINDTODEVICE={iface}")
        except PermissionError:
            sock.close()
            raise RuntimeError("SO_BINDTODEVICE 需要 root,请加 sudo,或改用 --bind")
    elif bind_ip:
        sock.bind((bind_ip, 0))
        log("[BIND]", f"bind 源地址={bind_ip}")

    sock.settimeout(connect_timeout)
    return sock


# ─────────────────────────── 接收线程 ───────────────────────────

def recv_thread(sock: socket.socket, stop: threading.Event) -> None:
    sock.settimeout(0.5)
    buf = b""
    last_log = 0.0
    total = 0
    try:
        while not stop.is_set():
            try:
                data = sock.recv(4096)
            except socket.timeout:
                if time.time() - last_log > 60 and total == 0:
                    last_log = time.time()
                    log("[RX]", "60s 无数据(空闲)")
                continue
            except OSError as e:
                if stop.is_set():
                    return
                log("[RX]", f"socket 错误: {e}")
                return

            if not data:
                log("[RX]", "对端关闭连接 (EOF)")
                stop.set()
                return

            total += len(data)
            buf += data
            # 按行(\r\n / \n)切, 顺便保留 hex
            while True:
                idx = -1
                for sep in (b"\r\n", b"\n"):
                    p = buf.find(sep)
                    if p >= 0 and (idx < 0 or p < idx):
                        idx = p
                if idx < 0:
                    break
                line = buf[:idx]
                buf = buf[idx + (2 if buf[idx:idx+2] == b"\r\n" else 1):]
                _print_chunk(line)
            # 残留过长 — 直接打印 hex 防止粘在缓冲区
            if len(buf) > 2048:
                _print_chunk(buf, force_hex=True)
                buf = b""
    finally:
        log("[RX]", f"接收线程退出,共收 {total} 字节")


def _print_chunk(data: bytes, force_hex: bool = False) -> None:
    if not data:
        return
    if force_hex:
        log("[RX-HEX]", data.hex())
        return
    try:
        text = data.decode("ascii")
        is_print = all(32 <= ord(c) < 127 or c in "\t " for c in text)
    except UnicodeDecodeError:
        is_print = False
        text = ""
    if is_print and text:
        log("[RX]", repr(text))
    else:
        log("[RX-HEX]", f"{len(data)}B: {data.hex(' ', 8)}")


# ─────────────────────────── 主连接逻辑 ───────────────────────────

def connect_with_cooldown(host: str, port: int, *,
                          iface: Optional[str], bind_ip: Optional[str],
                          max_attempts: int = 8,
                          stop: threading.Event) -> Optional[socket.socket]:
    delay = 1.0
    for n in range(1, max_attempts + 1):
        if stop.is_set():
            return None
        log("[CONN]", f"尝试 {n}/{max_attempts}: {host}:{port}")
        try:
            sock = make_socket(iface=iface, bind_ip=bind_ip, connect_timeout=3.0)
            sock.connect((host, port))
        except OSError as e:
            log("[CONN]", f"connect 失败: {e}")
            time.sleep(min(delay, 30))
            delay *= 2
            continue

        # MSG_PEEK 检测设备"冷却踢"
        sock.settimeout(0.8)
        try:
            probe = sock.recv(1, socket.MSG_PEEK)
            if probe == b"":
                wait = min(5 * n, 30)
                log("[CONN]", f"设备秒踢(冷却保护),{wait}s 后重试")
                sock.close()
                # 用 wait,但允许 stop 中断
                stop.wait(wait)
                continue
            elif probe:
                log("[CONN]", f"PEEK 收到 {len(probe)}B 数据(立刻有上报),保留")
        except socket.timeout:
            pass
        except OSError as e:
            log("[CONN]", f"PEEK 异常: {e},放弃这次")
            sock.close()
            time.sleep(min(delay, 30))
            delay *= 2
            continue

        sock.settimeout(None)
        log("[CONN]", f"连接成功: {host}:{port}")
        return sock
    log("[CONN]", "重试用尽,放弃")
    return None


# ─────────────────────────── 用户指令解析 ───────────────────────────

HELP = """\
可用命令:
  lon         发 b"LON\\r\\n"  (老款扫码器开扫)
  loff        发 b"LOFF\\r\\n" (老款扫码器停扫)
  raw <xxx>   原样发 xxx 后接 \\r\\n
  hex <hex>   按十六进制发原始字节,支持空格分隔  (例如 hex AABB CCDD)
  port <p>    断开当前并重连到端口 p (常用 55256/55266/55276/55286)
  status      打印当前 socket 状态
  help / ?    打印本帮助
  quit / q    退出(自动 LOFF)
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="WMax 扫码器 TCP 探针")
    ap.add_argument("--ip", required=True, help="扫码器 IP")
    ap.add_argument("--port", type=int, default=55256,
                    help="端口 (默认 55256, 老 LON 端口; 也可填 55266/55276/55286)")
    ap.add_argument("--iface", default=None,
                    help="Linux 强制 SO_BINDTODEVICE 网卡 (需 sudo)")
    ap.add_argument("--bind", dest="bind_ip", default=None,
                    help="bind 源 IP, 引导内核走对应网卡 (无需 sudo)")
    ap.add_argument("--auto-lon", action="store_true",
                    help="连上后立即自动发一次 LON")
    args = ap.parse_args()

    log("[ENV]",
        f"系统={platform.system()} 用户ID={os.geteuid() if hasattr(os,'geteuid') else 'N/A'}")
    diagnose_local_ifaces()
    diagnose_route(args.ip)

    stop = threading.Event()
    sock_holder: dict = {"sock": None, "rx": None}

    def cleanup_send_loff():
        sock = sock_holder.get("sock")
        if sock:
            try:
                sock.sendall(b"LOFF\r\n")
                log("[TX]", "退出前自动发 LOFF")
            except OSError:
                pass

    def signal_handler(_signum, _frame):
        log("[SIG]", "收到 Ctrl+C, 退出中...")
        stop.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    def _connect(port: int) -> bool:
        old = sock_holder.get("sock")
        if old:
            try:
                old.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                old.close()
            except OSError:
                pass
            sock_holder["sock"] = None
            if sock_holder.get("rx"):
                sock_holder["rx"].join(timeout=1.0)
                sock_holder["rx"] = None

        sock = connect_with_cooldown(args.ip, port,
                                     iface=args.iface, bind_ip=args.bind_ip,
                                     stop=stop)
        if not sock:
            return False
        sock_holder["sock"] = sock

        local_stop = threading.Event()
        t = threading.Thread(
            target=recv_thread, args=(sock, local_stop),
            name=f"rx-{args.ip}-{port}", daemon=True,
        )
        t.start()
        sock_holder["rx"] = t
        sock_holder["rx_stop"] = local_stop

        if args.auto_lon and port == 55256:
            try:
                sock.sendall(b"LON\r\n")
                log("[TX]", "auto-lon: b'LON\\r\\n'")
            except OSError as e:
                log("[TX]", f"auto-lon 发送失败: {e}")
        return True

    if not _connect(args.port):
        return 1

    log("[INFO]", "进入交互模式,输入 help 查看命令")
    sys.stdout.write("> "); sys.stdout.flush()

    while not stop.is_set():
        # 用 select 让 Ctrl+C 能立刻中断 input
        ready, _, _ = select.select([sys.stdin], [], [], 0.5)
        if not ready:
            continue
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            stop.set()
            break
        if not line:
            stop.set()
            break
        cmd = line.strip()
        if not cmd:
            sys.stdout.write("> "); sys.stdout.flush()
            continue

        try:
            _handle_cmd(cmd, sock_holder, _connect, stop)
        except Exception as e:
            log("[ERR]", f"命令异常: {e}")

        if not stop.is_set():
            sys.stdout.write("> "); sys.stdout.flush()

    cleanup_send_loff()
    rx = sock_holder.get("rx_stop")
    if rx:
        rx.set()
    sock = sock_holder.get("sock")
    if sock:
        try:
            sock.close()
        except OSError:
            pass
    log("[INFO]", "已退出")
    return 0


def _handle_cmd(cmd: str, sock_holder: dict,
                connect_fn, stop: threading.Event) -> None:
    sock: Optional[socket.socket] = sock_holder.get("sock")

    if cmd in ("q", "quit", "exit"):
        stop.set()
        return

    if cmd in ("?", "help", "h"):
        sys.stdout.write(HELP); sys.stdout.flush()
        return

    if cmd == "status":
        if not sock:
            log("[STAT]", "无活动 socket")
            return
        try:
            peer = sock.getpeername()
            local = sock.getsockname()
            log("[STAT]", f"local={local}  peer={peer}  fd={sock.fileno()}")
        except OSError as e:
            log("[STAT]", f"socket 已失效: {e}")
        return

    if cmd == "lon":
        _safe_send(sock, b"LON\r\n", "b'LON\\r\\n'")
        return

    if cmd == "loff":
        _safe_send(sock, b"LOFF\r\n", "b'LOFF\\r\\n'")
        return

    if cmd.startswith("raw "):
        payload = cmd[4:].encode("utf-8") + b"\r\n"
        _safe_send(sock, payload, repr(payload))
        return

    if cmd.startswith("hex "):
        h = cmd[4:].replace(" ", "").replace(":", "")
        try:
            payload = bytes.fromhex(h)
        except ValueError as e:
            log("[ERR]", f"hex 解析失败: {e}")
            return
        _safe_send(sock, payload, f"hex {len(payload)}B {payload.hex()}")
        return

    if cmd.startswith("port "):
        try:
            new_port = int(cmd[5:].strip())
        except ValueError:
            log("[ERR]", "port 后必须是数字")
            return
        log("[INFO]", f"切换端口到 {new_port}")
        connect_fn(new_port)
        return

    log("[ERR]", f"未知命令: {cmd!r} (输入 help 看说明)")


def _safe_send(sock: Optional[socket.socket], payload: bytes, desc: str) -> None:
    if not sock:
        log("[TX]", "socket 不可用,先 reconnect")
        return
    try:
        sock.sendall(payload)
        log("[TX]", f"已发送 {desc}")
    except OSError as e:
        log("[TX]", f"发送失败: {e}")


if __name__ == "__main__":
    sys.exit(main())
