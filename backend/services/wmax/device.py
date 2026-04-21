"""
WMax 设备管理 — 单台设备的 TCP 连接、命令收发、参数读写、图像获取

连接架构 (基于 Wireshark 逆向):
- CMD 端口 (55266): 命令收发
- IMG 端口 (55276): 图像流 (设备推送 + 客户端 ACK 流控)
- RPT 端口 (55286): 扫码报告上报

视频流协议 (逆向自原厂软件):
1. 同时连接 CMD + IMG + RPT 三端口
2. CMD 发送 GetConfigOpt{isAll: true}
3. CMD 发送 TurnOnOffVideo{bankId: 1, on: true}
4. 设备在 IMG 端口推送 SendImageNew(CmdType=49) 帧, 4字节DataLen
5. 客户端在 IMG 端口回复 10 字节 ACK 帧 (flag=IS_RESPONSE)
6. 设备在 RPT 端口推送 RptCode(CmdType=200) 帧
"""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .protocol import (
    CmdType, Command, DataReceiver, ZImage,
    pack, parse_new_image, parse_old_image,
    DEFAULT_FLAG, FLAG_IS_PROTOBUF, FLAG_IS_RESPONSE,
)
from . import messages as msg

logger = logging.getLogger(__name__)

DEFAULT_PORT = 55266
IMG_PORT = 55276
RPT_PORT = 55286
CONNECT_TIMEOUT = 3.0
RECV_TIMEOUT = 0.5
HEARTBEAT_INTERVAL = 10.0
MAX_RETRY_WAIT = 500

_CMD_NAMES = {v: k for k, v in CmdType.__members__.items()}


def _cmd_name(cmd_type: int) -> str:
    return _CMD_NAMES.get(cmd_type, f"Unknown({cmd_type})")


@dataclass
class DeviceInfo:
    sn: str = ""
    name: str = ""
    dev_type: int = 0
    hardware_version: str = ""
    app_version: str = ""
    ip: str = ""
    port: int = DEFAULT_PORT
    mac: str = ""


@dataclass
class DeviceState:
    connected: bool = False
    config: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    last_image: Optional[ZImage] = None
    last_code: Optional[dict] = None
    last_error: str = ""
    read_rate_result: Optional[dict] = None
    tune_done: bool = False


class WMaxDevice:
    """管理与单台 WMax 扫码器的完整通讯"""

    def __init__(self, ip: str, port: int = DEFAULT_PORT, sn: str = ""):
        self.ip = ip
        self.port = port
        self.sn = sn
        self._tag = f"[WMax {ip}:{port}]"

        self.info = DeviceInfo(ip=ip, port=port, sn=sn)
        self.state = DeviceState()

        # CMD 端口 (55266)
        self._sock: Optional[socket.socket] = None
        self._receiver = DataReceiver()
        # 抓包 (1111.pcapng/222.pcapng/333.pcapng) 证实真实 WMax 设备使用 3B DataLen 变体.
        # 发 4B DataLen 设备头校验通不过会 silent drop 整个命令, 导致所有请求超时.
        # receiver 会自动探测设备推送方向的格式, 我们主动发送先假定 3B.
        self._data_len_size = 3
        self._cmd_index = 0
        self._lock = threading.Lock()
        self._recv_thread: Optional[threading.Thread] = None

        # IMG 端口 (55276) — 图像流
        self._img_sock: Optional[socket.socket] = None
        self._img_receiver = DataReceiver()
        self._img_recv_thread: Optional[threading.Thread] = None
        self._video_active = False

        # RPT 端口 (55286) — 报告
        self._rpt_sock: Optional[socket.socket] = None
        self._rpt_receiver = DataReceiver()
        self._rpt_recv_thread: Optional[threading.Thread] = None

        self._stop_event = threading.Event()
        self._raw_config_data: Optional[bytes] = None

        self._pending_responses: dict[int, asyncio.Future] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self.on_code_received: Optional[Callable[[dict], None]] = None
        self.on_image_received: Optional[Callable[[ZImage], None]] = None
        self.on_disconnected: Optional[Callable[[], None]] = None

        self._good_read_flash = False
        self._flash_lock = threading.Lock()
        self._flash_off_timer: Optional[threading.Timer] = None
        self._last_flash_time: float = 0
        self._last_flash_code: str = ""

    # ── 连接管理 ─────────────────────────────────────────

    @staticmethod
    def _make_tcp_sock() -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(CONNECT_TIMEOUT)
        return sock

    @staticmethod
    def _set_keepalive(sock: socket.socket):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, "TCP_KEEPIDLE"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        except OSError:
            pass

    def connect(self, max_retries: int = 5) -> bool:
        if self.state.connected:
            logger.debug("%s 已连接，跳过重复连接", self._tag)
            return True

        for attempt in range(max_retries):
            logger.info("%s TCP 三端口连接中... (第 %d/%d 次)", self._tag, attempt + 1, max_retries)
            cmd_sock = img_sock = rpt_sock = None
            try:
                # 按抓包时序: CMD → IMG → RPT 快速依次连接
                cmd_sock = self._make_tcp_sock()
                cmd_sock.connect((self.ip, self.port))

                cmd_sock.settimeout(0.8)
                try:
                    probe = cmd_sock.recv(1, socket.MSG_PEEK)
                    if not probe:
                        wait = min(5 * (attempt + 1), 30)
                        logger.warning("%s 设备拒绝连接（冷却期），%ds 后重试", self._tag, wait)
                        cmd_sock.close()
                        time.sleep(wait)
                        continue
                except socket.timeout:
                    pass

                cmd_sock.settimeout(RECV_TIMEOUT)
                self._set_keepalive(cmd_sock)
                logger.info("%s CMD 55266 已连接", self._tag)

                try:
                    img_sock = self._make_tcp_sock()
                    img_sock.connect((self.ip, IMG_PORT))
                    img_sock.settimeout(RECV_TIMEOUT)
                    self._set_keepalive(img_sock)
                    logger.info("%s IMG 55276 已连接", self._tag)
                except Exception as e:
                    logger.warning("%s IMG 55276 连接失败: %s (仅CMD模式)", self._tag, e)
                    img_sock = None

                try:
                    rpt_sock = self._make_tcp_sock()
                    rpt_sock.connect((self.ip, RPT_PORT))
                    rpt_sock.settimeout(RECV_TIMEOUT)
                    self._set_keepalive(rpt_sock)
                    logger.info("%s RPT 55286 已连接", self._tag)
                except Exception as e:
                    logger.warning("%s RPT 55286 连接失败: %s", self._tag, e)
                    rpt_sock = None

                self._sock = cmd_sock
                self._img_sock = img_sock
                self._rpt_sock = rpt_sock
                self._stop_event.clear()
                self._receiver = DataReceiver()
                self._img_receiver = DataReceiver()
                self._rpt_receiver = DataReceiver()

                self._recv_thread = threading.Thread(
                    target=self._recv_loop, daemon=True,
                    name=f"wmax-cmd-{self.ip}")
                self._recv_thread.start()

                if img_sock:
                    self._img_recv_thread = threading.Thread(
                        target=self._img_recv_loop, daemon=True,
                        name=f"wmax-img-{self.ip}")
                    self._img_recv_thread.start()

                if rpt_sock:
                    self._rpt_recv_thread = threading.Thread(
                        target=self._rpt_recv_loop, daemon=True,
                        name=f"wmax-rpt-{self.ip}")
                    self._rpt_recv_thread.start()

                self.state.connected = True
                self.state.last_error = ""
                logger.info("%s 设备已连接 (CMD%s%s)",
                            self._tag,
                            "+IMG" if img_sock else "",
                            "+RPT" if rpt_sock else "")
                return True

            except Exception as e:
                self.state.last_error = str(e)
                logger.warning("%s 连接失败 (%d/%d): %s",
                               self._tag, attempt + 1, max_retries, e)
                for s in (cmd_sock, img_sock, rpt_sock):
                    if s:
                        try:
                            s.close()
                        except Exception:
                            pass
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))

        logger.error("%s 连接最终失败 (%d 次尝试)", self._tag, max_retries)
        return False

    def disconnect(self):
        logger.info("%s 断开连接 (三端口)", self._tag)
        self._stop_event.set()
        self._video_active = False
        self.state.connected = False

        socks = [
            ("CMD", self._sock),
            ("IMG", self._img_sock),
            ("RPT", self._rpt_sock),
        ]
        self._sock = None
        self._img_sock = None
        self._rpt_sock = None

        for label, sock in socks:
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        for thr in (self._recv_thread, self._img_recv_thread, self._rpt_recv_thread):
            if thr and thr.is_alive():
                thr.join(timeout=3.0)

        for label, sock in socks:
            if sock:
                try:
                    sock.setsockopt(
                        socket.SOL_SOCKET, socket.SO_LINGER,
                        struct.pack('ii', 1, 0))
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass

        pending = len(self._pending_responses)
        if pending:
            logger.warning("%s 断开时有 %d 个待响应命令", self._tag, pending)
            self._pending_responses.clear()

    def _recv_loop(self):
        logger.info("%s 接收线程启动", self._tag)
        last_activity = time.time()
        recv_bytes_total = 0
        cmd_count = 0

        while not self._stop_event.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                data = sock.recv(65536)
                if not data:
                    logger.warning("%s 收到空数据，连接已关闭", self._tag)
                    break
                recv_bytes_total += len(data)
                last_activity = time.time()
                cmds = self._receiver.feed(data)
                for cmd in cmds:
                    cmd_count += 1
                    self._dispatch_command(cmd)
            except socket.timeout:
                idle = time.time() - last_activity
                if idle > HEARTBEAT_INTERVAL:
                    logger.debug("%s 空闲 %.1fs，发送心跳(HandShake)", self._tag, idle)
                    try:
                        hb_cmd = Command(
                            cmd_type=CmdType.HandShake,
                            cmd_index=0,
                            flag=DEFAULT_FLAG,
                        )
                        self._send_raw(pack(hb_cmd, data_len_size=self._data_len_size))
                        last_activity = time.time()
                    except OSError as e:
                        logger.error("%s 心跳发送失败: %s", self._tag, e)
                        break
                continue
            except OSError as e:
                logger.error("%s 接收线程 socket 错误: %s", self._tag, e)
                break
            except Exception as e:
                logger.error("%s 接收线程异常: %s\n%s", self._tag, e, traceback.format_exc())
                break

        logger.info("%s 接收线程退出，总收 %d 字节，处理 %d 条命令",
                    self._tag, recv_bytes_total, cmd_count)

        if self.state.connected:
            self.state.connected = False
            logger.warning("%s 连接意外断开", self._tag)
            if self.on_disconnected:
                try:
                    self.on_disconnected()
                except Exception:
                    pass

    def _send_img_ack(self, cmd_type: int, cmd_index: int):
        """在 IMG 端口发送 10 字节 ACK 帧 (流控确认)"""
        flag = FLAG_IS_RESPONSE
        buf = bytearray(b"\x5A\x5A")
        buf += struct.pack(">I", flag)
        buf.append(cmd_type & 0xFF)
        buf.append(cmd_index & 0xFF)
        hdr_chk = sum(buf[2:]) & 0xFF
        buf.append(hdr_chk)
        buf.append(0xA5)

        sock = self._img_sock
        if sock:
            try:
                sock.sendall(bytes(buf))
            except OSError as e:
                logger.error("%s IMG ACK 发送失败: %s", self._tag, e)

    def _img_recv_loop(self):
        """IMG 端口 (55276) 接收线程: 接收图像帧并发送 ACK"""
        logger.info("%s IMG 接收线程启动", self._tag)
        recv_total = 0
        frame_count = 0

        while not self._stop_event.is_set():
            sock = self._img_sock
            if sock is None:
                break
            try:
                data = sock.recv(131072)
                if not data:
                    logger.warning("%s IMG 端口连接关闭", self._tag)
                    break
                recv_total += len(data)
                cmds = self._img_receiver.feed(data)
                for cmd in cmds:
                    if cmd.cmd_type == CmdType.SendImageNew and cmd.data_part:
                        image = parse_new_image(cmd.data_part)
                        if image:
                            frame_count += 1
                            self.state.last_image = image
                            if self.on_image_received:
                                try:
                                    self.on_image_received(image)
                                except Exception:
                                    pass
                        self._send_img_ack(CmdType.SendImageNew, cmd.cmd_index)

                    elif cmd.cmd_type == CmdType.SendImagePtcol and cmd.data_part:
                        image = parse_old_image(cmd.data_part)
                        if image:
                            frame_count += 1
                            self.state.last_image = image
                            if self.on_image_received:
                                try:
                                    self.on_image_received(image)
                                except Exception:
                                    pass
                        self._send_img_ack(CmdType.SendImagePtcol, cmd.cmd_index)

            except socket.timeout:
                continue
            except OSError as e:
                if not self._stop_event.is_set():
                    logger.error("%s IMG 线程 socket 错误: %s", self._tag, e)
                break

        logger.info("%s IMG 线程退出, 收 %dB, %d 帧",
                    self._tag, recv_total, frame_count)

    def _rpt_recv_loop(self):
        """RPT 端口 (55286) 接收线程: 接收扫码报告"""
        logger.info("%s RPT 接收线程启动", self._tag)

        while not self._stop_event.is_set():
            sock = self._rpt_sock
            if sock is None:
                break
            try:
                data = sock.recv(4096)
                if not data:
                    logger.warning("%s RPT 端口连接关闭", self._tag)
                    break
                cmds = self._rpt_receiver.feed(data)
                for cmd in cmds:
                    if cmd.cmd_type == CmdType.RptCode and cmd.data_part:
                        try:
                            dlen = len(cmd.data_part)
                            if dlen < 80:
                                continue
                            code_info = msg.decode_rpt_code(cmd.data_part)
                            codes = code_info.get("codes", [])
                            if codes:
                                codes_str = ", ".join(c.get("data", "?") for c in codes)
                                logger.warning("%s [RPT] 扫码结果: [%s] (%dB)",
                                               self._tag, codes_str, dlen)
                                self.state.last_code = code_info
                                if self._good_read_flash:
                                    self._flash_good_read(codes_str)
                                if self.on_code_received:
                                    self.on_code_received(code_info)
                        except Exception as e:
                            logger.debug("%s [RPT] RptCode 解码: %s", self._tag, e)
            except socket.timeout:
                continue
            except OSError as e:
                if not self._stop_event.is_set():
                    logger.error("%s RPT 线程 socket 错误: %s", self._tag, e)
                break

        logger.info("%s RPT 线程退出", self._tag)

    def _flash_good_read(self, code_str: str = "", cooldown: float = 3.0):
        """扫到新码时通过短暂关闭/重启视频制造可见的灯光闪烁
        cooldown: 同一个码的闪烁冷却时间(秒)，避免连续扫同一码时不停闪"""
        now = time.time()
        if code_str == self._last_flash_code and (now - self._last_flash_time) < cooldown:
            return
        with self._flash_lock:
            if self._flash_off_timer:
                self._flash_off_timer.cancel()
                self._flash_off_timer = None
            self._last_flash_time = now
            self._last_flash_code = code_str
            try:
                data_off = msg.encode_turn_on_off_video(False, 1)
                self.send_command(CmdType.TurnOnOffVideo, data_off)
                logger.warning("%s [FLASH] 灭灯 (Video OFF)", self._tag)
            except Exception as e:
                logger.warning("%s [FLASH] Video OFF 失败: %s", self._tag, e)
                return
            def _restore():
                try:
                    data_on = msg.encode_turn_on_off_video(True, 1)
                    self.send_command(CmdType.TurnOnOffVideo, data_on)
                    logger.warning("%s [FLASH] 亮灯 (Video ON)", self._tag)
                except Exception as e:
                    logger.warning("%s [FLASH] Video ON 失败: %s", self._tag, e)
            self._flash_off_timer = threading.Timer(0.15, _restore)
            self._flash_off_timer.daemon = True
            self._flash_off_timer.start()

    def _dispatch_command(self, cmd: Command):
        dl = self._receiver.detected_data_len_size
        if dl != self._data_len_size:
            logger.info("%s 检测到设备 DataLen=%d 字节，切换发送格式", self._tag, dl)
            self._data_len_size = dl

        name = _cmd_name(cmd.cmd_type)
        data_len = len(cmd.data_part) if cmd.data_part else 0
        is_resp = "响应" if cmd.is_response else "上报"

        if cmd.cmd_type == CmdType.SendImageNew and cmd.data_part:
            image = parse_new_image(cmd.data_part)
            if image:
                logger.debug("%s 收到新图像: %dx%d fmt=%d len=%d",
                             self._tag, image.width, image.height,
                             image.image_format, len(image.image_data))
                self.state.last_image = image
                if self.on_image_received:
                    self.on_image_received(image)
            else:
                logger.warning("%s 新图像解析失败，数据长度=%d", self._tag, data_len)
            return

        if cmd.cmd_type == CmdType.SendImagePtcol and cmd.data_part:
            image = parse_old_image(cmd.data_part)
            if image:
                logger.debug("%s 收到旧格式图像: %dx%d", self._tag, image.width, image.height)
                self.state.last_image = image
                if self.on_image_received:
                    self.on_image_received(image)
            return

        if cmd.cmd_type == CmdType.RptCode and cmd.data_part:
            try:
                code_info = msg.decode_rpt_code(cmd.data_part)
                codes_str = ", ".join(c.get("data", "?") for c in code_info.get("codes", []))
                logger.info("%s 扫码结果: [%s]", self._tag, codes_str)
                self.state.last_code = code_info
                if self.on_code_received:
                    self.on_code_received(code_info)
            except Exception as e:
                logger.error("%s RptCode 解码失败: %s", self._tag, e)
            return

        if cmd.cmd_type == CmdType.RptReadRateTest and cmd.data_part:
            try:
                rr = msg.decode_rpt_read_rate(cmd.data_part)
                logger.debug("%s 读码率测试: total=%s ok=%s rate=%s",
                             self._tag, rr.get("total_count"), rr.get("success_count"), rr.get("rate"))
                self.state.read_rate_result = rr
            except Exception as e:
                logger.error("%s RptReadRateTest 解码失败: %s", self._tag, e)
            return

        if cmd.cmd_type == CmdType.RptTuneResult and cmd.data_part:
            logger.info("%s 自动调参完成", self._tag)
            self.state.tune_done = True
            return

        if cmd.is_response and cmd.cmd_index in self._pending_responses:
            logger.debug("%s 收到%s idx=%d cmd=%s data=%dB",
                         self._tag, is_resp, cmd.cmd_index, name, data_len)
            fut = self._pending_responses.pop(cmd.cmd_index)
            if not fut.done():
                try:
                    loop = fut.get_loop()
                    loop.call_soon_threadsafe(fut.set_result, cmd)
                except Exception as e:
                    logger.error("%s 设置 Future 失败: %s", self._tag, e)
            return

        if cmd.cmd_type not in (CmdType.SendImageNew, CmdType.SendImagePtcol):
            logger.debug("%s 收到未处理命令: %s idx=%d %s data=%dB",
                         self._tag, name, cmd.cmd_index, is_resp, data_len)

    # ── 底层发送 ─────────────────────────────────────────

    def _send_raw(self, data: bytes):
        with self._lock:
            if self._sock:
                self._sock.sendall(data)
            else:
                logger.warning("%s 发送失败: socket 已关闭", self._tag)

    def _next_index(self) -> int:
        self._cmd_index = (self._cmd_index + 1) & 0xFF
        return self._cmd_index

    def send_command(self, cmd_type: int, data: bytes = None,
                     is_response: bool = False) -> Command:
        cmd = Command(
            cmd_type=cmd_type,
            cmd_index=self._next_index(),
            flag=DEFAULT_FLAG,
            data_part=data,
        )
        if is_response:
            cmd.is_response = True

        frame = pack(cmd, data_len_size=self._data_len_size)
        logger.debug("%s 发送命令 %s idx=%d data=%dB frame=%dB dl=%d",
                     self._tag, _cmd_name(cmd_type), cmd.cmd_index,
                     len(data) if data else 0, len(frame), self._data_len_size)
        self._send_raw(frame)
        return cmd

    async def send_and_wait(self, cmd_type: int, data: bytes = None,
                            timeout: float = 5.0) -> Optional[Command]:
        cmd = Command(
            cmd_type=cmd_type,
            cmd_index=self._next_index(),
            flag=DEFAULT_FLAG,
            data_part=data,
        )
        frame = pack(cmd, data_len_size=self._data_len_size)

        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._pending_responses[cmd.cmd_index] = fut

        name = _cmd_name(cmd_type)
        logger.debug("%s 发送+等待 %s idx=%d timeout=%.1fs data=%dB dl=%d",
                     self._tag, name, cmd.cmd_index, timeout,
                     len(data) if data else 0, self._data_len_size)
        self._send_raw(frame)

        try:
            resp = await asyncio.wait_for(fut, timeout=timeout)
            resp_len = len(resp.data_part) if resp.data_part else 0
            logger.debug("%s %s 响应已收到 idx=%d data=%dB",
                         self._tag, name, resp.cmd_index, resp_len)
            return resp
        except asyncio.TimeoutError:
            self._pending_responses.pop(cmd.cmd_index, None)
            logger.warning("%s %s 响应超时 (%.1fs)，idx=%d",
                           self._tag, name, timeout, cmd.cmd_index)
            return None

    # ── 高层 API ─────────────────────────────────────────

    async def handshake(self) -> dict:
        logger.info("%s 发起握手", self._tag)
        resp = await self.send_and_wait(CmdType.HandShake, timeout=3.0)
        if resp and resp.data_part:
            result = msg.decode_handshake_resp(resp.data_part)
            logger.info("%s 握手成功: protocol_version=%s ret_code=%s",
                        self._tag, result.get("protocol_version"),
                        result.get("response", {}).get("ret_code"))
            return result
        logger.warning("%s 握手失败: 无响应", self._tag)
        return {}

    async def activate_rpt_reporting(self):
        """激活 RPT 条码推送.

        按抓包时序 (1111.pcapng) 先发 GetConfigOpt, 再发 TurnOnOffVideo:
        - GetConfigOpt 让设备进入"工作态" + 让 receiver 探测 3/4B DataLen 变体
        - TurnOnOffVideo on=True 激活图像流和 RPT 条码流
        省略 IDManager 也没发的 HandShake (设备对 CmdType=3 无响应, 只会超时).
        """
        try:
            cfg = await self.load_config()
            if not cfg:
                logger.warning("%s GetConfigOpt 无响应, 可能不是 WMax 或设备未就绪", self._tag)
                # 仍继续尝试 TurnOnOffVideo, 有些设备变体直接就能工作
            ok = await self.turn_on_video(on=True, bank_id=1)
            if ok:
                logger.warning("%s RPT 报告已激活 (GetConfigOpt + TurnOnOffVideo on)", self._tag)
            else:
                logger.warning("%s RPT 激活失败 (TurnOnOffVideo 无响应)", self._tag)
            return ok
        except Exception as e:
            logger.warning("%s RPT 激活异常: %s", self._tag, e)
            return False

    async def load_config(self, config_id: int = -1) -> dict:
        logger.info("%s 读取配置 config_id=%d", self._tag, config_id)
        # 必须带 payload: 抓包证实不带 payload 或带 field1=-1 设备都不响应.
        data = msg.encode_get_config_opt(config_id, inc_global=True)
        resp = await self.send_and_wait(CmdType.GetConfigOpt, data, timeout=8.0)
        if resp and resp.data_part:
            try:
                self._raw_config_data = resp.data_part
                config = msg.decode_config_opt_resp(resp.data_part)
                sections = [k for k in config if k not in ("raw_fields",) and config[k]]
                logger.info("%s 配置已读取: %d 字节，包含 %s",
                            self._tag, len(resp.data_part), ", ".join(sections))
                self.state.config = config
                return config
            except Exception as e:
                logger.error("%s 配置解码失败: %s\n%s", self._tag, e, traceback.format_exc())
                return {"error": str(e)}
        logger.warning("%s 读取配置失败: 无响应", self._tag)
        return {}

    async def get_device_features(self) -> dict:
        logger.info("%s 查询设备特性", self._tag)
        resp = await self.send_and_wait(CmdType.DeviceFeature, timeout=3.0)
        if resp and resp.data_part:
            fields = msg.decode_message(resp.data_part)
            self.state.features = {"raw_fields": list(fields.keys())}
            logger.info("%s 设备特性: fields=%s", self._tag, list(fields.keys()))
            return self.state.features
        logger.info("%s 设备不支持 DeviceFeature 查询（VS601 等型号）", self._tag)
        return {}

    def _build_config_payload(self, sensor_params: dict = None,
                              light_params: dict = None,
                              common_params: dict = None,
                              code_params: dict = None,
                              reading_params: dict = None,
                              input_params: dict = None,
                              output_params: dict = None,
                              indicator_params: dict = None,
                              data_output_format_params: dict = None,
                              startup_cfg_id: int = -1,
                              config_id: int = -1) -> bytes:
        parts = []
        if sensor_params: parts.append("sensor")
        if light_params: parts.append("light")
        if common_params: parts.append("common")
        if code_params: parts.append("code")
        if reading_params: parts.append("reading")
        if input_params: parts.append("input")
        if output_params: parts.append("output")
        if indicator_params: parts.append("indicator")
        if data_output_format_params: parts.append("data_output_format")
        logger.debug("%s 构建配置 payload: %s startup=%d cfg=%d",
                     self._tag, "+".join(parts) or "(空)",
                     startup_cfg_id, config_id)

        raw = getattr(self, '_raw_config_data', None)
        if raw:
            return self._patch_config_payload(
                raw, sensor_params, light_params, common_params, code_params,
                reading_params, input_params, output_params, indicator_params,
                data_output_format_params, startup_cfg_id, config_id)

        logger.warning("%s 无缓存配置，使用从零构建模式", self._tag)
        return self._build_config_from_scratch(
            sensor_params, light_params, common_params, code_params,
            reading_params, input_params, output_params, indicator_params,
            data_output_format_params, startup_cfg_id, config_id)

    def _patch_config_payload(self, raw_resp: bytes,
                              sensor_params, light_params, common_params,
                              code_params, reading_params, input_params,
                              output_params, indicator_params,
                              data_output_format_params,
                              startup_cfg_id, config_id) -> bytes:
        """基于设备原始 GetConfigOpt 响应做字段级 patch，保留所有未修改字段。"""
        top = msg.decode_message(raw_resp)
        normal_raw = msg.get_bytes(top, 2)
        if not normal_raw:
            logger.warning("%s patch 失败: 响应无 field 2 (normal_config)", self._tag)
            return self._build_config_from_scratch(
                sensor_params, light_params, common_params, code_params,
                reading_params, input_params, output_params, indicator_params,
                data_output_format_params, startup_cfg_id, config_id)

        normal_patches = {}

        if sensor_params or light_params or common_params or code_params:
            bank_opt_raw = msg.get_bytes(msg.decode_message(normal_raw), 3)
            if bank_opt_raw:
                patched_bank_opt = self._patch_bank_opt(
                    bank_opt_raw, sensor_params, light_params, common_params, code_params)
                normal_patches[3] = msg.encode_field_message(3, patched_bank_opt)

        if output_params:
            normal_patches[5] = msg.encode_field_message(
                5, msg.encode_output_opt_wrapper(output_params))

        if reading_params:
            normal_patches[8] = msg.encode_field_message(
                8, msg.encode_reading_opt(reading_params))

        if indicator_params and "mode" in indicator_params:
            normal_patches[9] = msg.encode_field_message(
                9, msg.encode_indicator_opt(int(indicator_params["mode"])))

        if data_output_format_params:
            normal_patches[10] = msg.encode_field_message(
                10, msg.encode_data_output_format(data_output_format_params))

        if input_params:
            normal_patches[4] = msg.encode_field_message(
                4, msg.encode_input_opt_wrapper(input_params))

        patched_normal = msg.patch_message(normal_raw, normal_patches)
        logger.info("%s patch normal: %d → %d 字节 (%d 个字段被替换)",
                    self._tag, len(normal_raw), len(patched_normal), len(normal_patches))

        set_config = msg.encode_field_message(1, patched_normal)
        if startup_cfg_id >= 0:
            set_config += msg.encode_wrapper_int32(3, startup_cfg_id)
        if config_id >= 0:
            set_config += msg.encode_wrapper_int32(4, config_id)

        logger.debug("%s 配置 payload 已构建(patch模式): %d 字节", self._tag, len(set_config))
        return set_config

    def _patch_bank_opt(self, bank_opt_raw: bytes,
                        sensor_params, light_params, common_params, code_params) -> bytes:
        """在 BankOpt 中 patch 第一个 bank 的子字段。"""
        bank_fields = msg.decode_message(bank_opt_raw)
        banks_raw = msg.get_all_bytes(bank_fields, 2) or msg.get_all_bytes(bank_fields, 1)
        if not banks_raw:
            return bank_opt_raw

        bank_field_num = 2 if msg.get_all_bytes(bank_fields, 2) else 1
        first_bank = banks_raw[0]
        bank_patches = {}

        if common_params:
            bank_patches[2] = msg.encode_field_message(2, msg.encode_common_opt(common_params))
        if code_params:
            bank_patches[3] = msg.encode_field_message(3, msg.encode_code_opt(code_params))
        if light_params:
            bank_patches[4] = msg.encode_field_message(4, msg.encode_light_opt(light_params))
        if sensor_params:
            bank_patches[5] = msg.encode_field_message(5, msg.encode_sensor_opt(sensor_params))

        patched_bank = msg.patch_message(first_bank, bank_patches)

        result = bytearray()
        result.extend(msg.encode_field_message(bank_field_num, patched_bank))
        for bank_raw in banks_raw[1:]:
            result.extend(msg.encode_field_message(bank_field_num, bank_raw))

        for fn, vals in bank_fields.items():
            if fn == bank_field_num:
                continue
            for v in vals:
                if isinstance(v, int):
                    result.extend(msg.encode_field_varint(fn, v))
                elif isinstance(v, bytes):
                    result.extend(msg.encode_field_message(fn, v))
        return bytes(result)

    def _build_config_from_scratch(self, sensor_params, light_params,
                                    common_params, code_params, reading_params,
                                    input_params, output_params, indicator_params,
                                    data_output_format_params,
                                    startup_cfg_id, config_id) -> bytes:
        """从零构建配置（无缓存时的 fallback）。"""
        sensor_bytes = msg.encode_sensor_opt(sensor_params) if sensor_params else b""
        light_bytes = msg.encode_light_opt(light_params) if light_params else b""
        common_bytes = msg.encode_common_opt(common_params) if common_params else b""
        code_bytes = msg.encode_code_opt(code_params) if code_params else b""
        bank = msg.encode_bank_channel_opt(sensor_bytes, light_bytes,
                                           common_bytes, code_bytes)
        bank_opt = msg.encode_bank_opt([bank])
        reading_bytes = msg.encode_reading_opt(reading_params) if reading_params else b""
        input_bytes = msg.encode_input_opt_wrapper(input_params) if input_params else b""
        output_bytes = msg.encode_output_opt_wrapper(output_params) if output_params else b""
        indicator_bytes = b""
        if indicator_params and "mode" in indicator_params:
            indicator_bytes = msg.encode_indicator_opt(int(indicator_params["mode"]))
        data_output_format_bytes = b""
        if data_output_format_params:
            data_output_format_bytes = msg.encode_data_output_format(data_output_format_params)
        normal = msg.encode_normal_config_opt(
            bank_opt, input_bytes, output_bytes, reading_bytes,
            indicator_bytes, data_output_format_bytes)
        payload = msg.encode_set_config_opt(normal, b"", startup_cfg_id, config_id)
        logger.debug("%s 配置 payload 已构建(从零): %d 字节", self._tag, len(payload))
        return payload

    def _check_resp_success(self, resp) -> bool:
        if resp and resp.data_part:
            r = msg.decode_message(resp.data_part)
            resp_base = msg.get_bytes(r, 1)
            if resp_base:
                rb = msg.decode_resp_base(resp_base)
                code = rb.get("ret_code", 0)
                ok = code == 1
                if not ok:
                    logger.warning("%s 设备返回 ret_code=%d (非成功)", self._tag, code)
                return ok
        logger.warning("%s 响应检查失败: 无 resp_base", self._tag)
        return False

    async def set_params(self, sensor_params: dict = None,
                         light_params: dict = None,
                         common_params: dict = None,
                         code_params: dict = None,
                         reading_params: dict = None,
                         input_params: dict = None,
                         output_params: dict = None,
                         indicator_params: dict = None,
                         data_output_format_params: dict = None) -> bool:
        logger.info("%s 下发参数（不保存）", self._tag)
        try:
            if not getattr(self, '_raw_config_data', None):
                logger.info("%s 无缓存配置，先读取设备配置", self._tag)
                await self.load_config()
            config = self._build_config_payload(
                sensor_params, light_params, common_params, code_params,
                reading_params, input_params, output_params,
                indicator_params, data_output_format_params)
            resp = await self.send_and_wait(CmdType.SetConfigOpt, config, timeout=5.0)
            ok = self._check_resp_success(resp)
            logger.info("%s 下发参数结果: %s", self._tag, "成功" if ok else "失败")
            if ok:
                await self.load_config()
            return ok
        except Exception as e:
            logger.error("%s 下发参数异常: %s\n%s", self._tag, e, traceback.format_exc())
            return False

    async def save_config(self, sensor_params: dict = None,
                          light_params: dict = None,
                          startup_cfg_id: int = 0,
                          config_id: int = 0,
                          common_params: dict = None,
                          code_params: dict = None,
                          reading_params: dict = None,
                          input_params: dict = None,
                          output_params: dict = None,
                          indicator_params: dict = None,
                          data_output_format_params: dict = None) -> bool:
        """保存配置到 Flash。

        使用 SetConfigOpt 带 startup_cfg_id/config_id 标志，
        部分设备不支持 CmdType.SaveConfig(55) 但支持此方式。
        """
        logger.info("%s 保存参数到 Flash (startup=%d cfg=%d)", self._tag,
                    startup_cfg_id, config_id)
        try:
            if not getattr(self, '_raw_config_data', None):
                logger.info("%s 无缓存配置，先读取设备配置", self._tag)
                await self.load_config()
            payload = self._build_config_payload(
                sensor_params, light_params, common_params, code_params,
                reading_params, input_params, output_params,
                indicator_params, data_output_format_params,
                startup_cfg_id=startup_cfg_id, config_id=config_id)
            resp = await self.send_and_wait(CmdType.SetConfigOpt, payload, timeout=8.0)
            ok = self._check_resp_success(resp)
            if not ok:
                logger.info("%s SetConfigOpt(save) 失败，回退到 SaveConfig(55)", self._tag)
                resp = await self.send_and_wait(CmdType.SaveConfig, payload, timeout=10.0)
                ok = resp is not None
            logger.info("%s 保存配置结果: %s", self._tag, "成功" if ok else "失败")
            return ok
        except Exception as e:
            logger.error("%s 保存配置异常: %s\n%s", self._tag, e, traceback.format_exc())
            return False

    async def start_read_rate_test(self) -> bool:
        logger.info("%s 开始读码率测试", self._tag)
        resp = await self.send_and_wait(CmdType.CtrlRRTest, timeout=3.0)
        return resp is not None

    async def stop_read_rate_test(self) -> bool:
        logger.info("%s 停止读码率测试", self._tag)
        resp = await self.send_and_wait(CmdType.CtrlRRTest, b"\x00", timeout=3.0)
        return resp is not None

    async def turn_on_video(self, on: bool = True, bank_id: int = 1) -> bool:
        logger.info("%s %s视频流 (bankId=%d, IMG端口%s)",
                    self._tag, "开启" if on else "关闭", bank_id,
                    "已连接" if self._img_sock else "未连接")
        data = msg.encode_turn_on_off_video(on, bank_id)
        resp = await self.send_and_wait(CmdType.TurnOnOffVideo, data, timeout=3.0)
        if resp is not None:
            self._video_active = on
        return resp is not None

    async def auto_focus(self, start: bool = True) -> bool:
        logger.info("%s %s自动对焦", self._tag, "开始" if start else "停止")
        data = msg.encode_auto_focus(start)
        resp = await self.send_and_wait(CmdType.AutoFocus, data, timeout=5.0)
        return resp is not None

    async def start_tune(self) -> bool:
        logger.info("%s 开始自动调参", self._tag)
        data = msg.encode_start_tune()
        resp = await self.send_and_wait(CmdType.StartTune, data, timeout=30.0)
        return resp is not None

    async def cancel_tune(self) -> bool:
        logger.info("%s 取消自动调参", self._tag)
        data = msg.encode_cancel_tune()
        resp = await self.send_and_wait(CmdType.CancelTune, data, timeout=3.0)
        return resp is not None

    def trigger_on(self):
        """触发扫码开始 (同步, fire-and-forget). 三种命令兜底:
        - TurnOnOffVideo(on=True, bankId=1) : 已验证真机能让扫码器持续扫码
        - Trigger(on=True) : WMax 协议专用触发命令
        - SendTermCmd("LON") : 文本命令兼容旧固件
        三个都发, 哪个被扫码器接受就哪个生效."""
        logger.info("%s 触发 ON (TurnOnOffVideo + Trigger + LON)", self._tag)
        try:
            self.send_command(CmdType.TurnOnOffVideo,
                              msg.encode_turn_on_off_video(True, 1))
        except Exception as e:
            logger.warning("%s TurnOnOffVideo(on) 发送失败: %s", self._tag, e)
        try:
            self.send_command(CmdType.Trigger, msg.encode_trigger(True))
        except Exception as e:
            logger.warning("%s Trigger(on) 发送失败: %s", self._tag, e)
        try:
            self.send_command(CmdType.SendTermCmd,
                              msg.encode_send_term_cmd("LON"))
        except Exception as e:
            logger.warning("%s SendTermCmd(LON) 发送失败: %s", self._tag, e)

    def trigger_off(self):
        """关闭扫码 (同步). 三种命令兜底, 见 trigger_on 注释."""
        logger.info("%s 触发 OFF (TurnOnOffVideo + Trigger + LOFF)", self._tag)
        try:
            self.send_command(CmdType.TurnOnOffVideo,
                              msg.encode_turn_on_off_video(False, 1))
        except Exception as e:
            logger.warning("%s TurnOnOffVideo(off) 发送失败: %s", self._tag, e)
        try:
            self.send_command(CmdType.Trigger, msg.encode_trigger(False))
        except Exception as e:
            logger.warning("%s Trigger(off) 发送失败: %s", self._tag, e)
        try:
            self.send_command(CmdType.SendTermCmd,
                              msg.encode_send_term_cmd("LOFF"))
        except Exception as e:
            logger.warning("%s SendTermCmd(LOFF) 发送失败: %s", self._tag, e)

    async def flash_and_scan(self, duration: float = 5.0) -> list[str]:
        """测试用: 让扫码器打光 duration 秒, 期间收集扫到的条码, 结束后关灯.
        打光策略 (多指令兜底, 哪个生效由扫码器固件决定):
        1. TurnOnOffVideo(on=True) — 进入持续扫描模式, 已验证在真机能让扫码器持续闪
        2. Trigger(on=True) + SendTermCmd(LON) — 兼容不同固件
        结束时全部 off.
        不会丢失原 on_code_received 回调, 测试期间同时触发原回调."""
        import asyncio as _asyncio
        collected: list[str] = []
        seen: set = set()

        orig_cb = self.on_code_received
        def _tap(code_info: dict):
            try:
                for c in code_info.get("codes", []):
                    raw = (c.get("data") or "").strip()
                    if raw and raw not in seen:
                        seen.add(raw)
                        collected.append(raw)
            except Exception:
                pass
            if orig_cb is not None:
                try:
                    orig_cb(code_info)
                except Exception as e:
                    logger.warning("%s 原 on_code_received 回调异常: %s", self._tag, e)

        self.on_code_received = _tap
        try:
            # 1) 先 turn_on_video (已知能让扫码器进入持续扫描)
            try:
                await self.turn_on_video(on=True, bank_id=1)
            except Exception as e:
                logger.warning("%s turn_on_video(on) 失败: %s", self._tag, e)
            # 2) 再发 Trigger + LON 兜底
            try:
                self.trigger_on()
            except Exception as e:
                logger.warning("%s trigger_on 失败: %s", self._tag, e)
            await _asyncio.sleep(duration)
        finally:
            try:
                self.trigger_off()
            except Exception as e:
                logger.warning("%s trigger_off 失败: %s", self._tag, e)
            try:
                await self.turn_on_video(on=False, bank_id=1)
            except Exception as e:
                logger.warning("%s turn_on_video(off) 失败: %s", self._tag, e)
            self.on_code_received = orig_cb
        return collected

    async def reboot(self) -> bool:
        logger.warning("%s 重启设备!", self._tag)
        resp = await self.send_and_wait(CmdType.CtrlReboot, timeout=3.0)
        return resp is not None

    async def reset_to_default(self, reset_bank: bool = True,
                               reset_ip: bool = False,
                               reset_other: bool = True) -> bool:
        logger.warning("%s 恢复出厂设置! bank=%s ip=%s other=%s",
                       self._tag, reset_bank, reset_ip, reset_other)
        data = msg.encode_dev_reset(reset_bank, reset_ip, reset_other)
        resp = await self.send_and_wait(CmdType.CtrlReset, data, timeout=5.0)
        return resp is not None

    async def indicate_device(self) -> bool:
        logger.info("%s 闪灯定位", self._tag)
        resp = await self.send_and_wait(CmdType.IndicateDev, timeout=3.0)
        if resp is None:
            logger.info("%s 设备不支持 IndicateDev（VS601 等型号）", self._tag)
        return resp is not None

    async def set_run_mode(self, mode: int = 0, bank_id: int = 0,
                           start: bool = True, image: bool = True) -> bool:
        logger.info("%s 设置运行模式: mode=%d bank=%d start=%s image=%s",
                    self._tag, mode, bank_id, start, image)
        data = msg.encode_run_mode(mode, bank_id, start, image)
        resp = await self.send_and_wait(CmdType.DeviceRunMode, data, timeout=5.0)
        return resp is not None

    async def turn_on_trigger_image(self, on: bool = True) -> bool:
        logger.info("%s %s触发图像", self._tag, "开启" if on else "关闭")
        data = msg.encode_wrapper_bool(1, on)
        resp = await self.send_and_wait(CmdType.TurnOnOffTrggerImage, data, timeout=3.0)
        return resp is not None

    def get_last_image(self) -> Optional[ZImage]:
        return self.state.last_image

    def get_last_code(self) -> Optional[dict]:
        return self.state.last_code

    def get_status(self) -> dict:
        return {
            "connected": self.state.connected,
            "ip": self.ip,
            "port": self.port,
            "sn": self.info.sn,
            "name": self.info.name,
            "last_error": self.state.last_error,
            "has_config": bool(self.state.config),
            "video_active": self._video_active,
            "img_port_connected": self._img_sock is not None,
            "rpt_port_connected": self._rpt_sock is not None,
        }
