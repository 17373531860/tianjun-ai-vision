"""
WMax 设备 UDP 发现

三步发现策略（依次尝试，任一成功即可）:
1. 子网广播: 向所有本地网卡的广播地址:55266 发送 FindDevice
2. 全局广播: 向 255.255.255.255:55266 发送 FindDevice
3. 子网扫描: 对常见网段单播 FindDevice 到 x.x.x.1~254（保底）

使用线程池执行阻塞 UDP recvfrom，兼容所有 Python 版本。
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor

from .protocol import (
    pack, Command, CmdType, DataReceiver,
)
from .messages import decode_find_device_resp

logger = logging.getLogger(__name__)

UDP_DISCOVERY_PORT = 55266
TEXT_DISCOVERY_PORT = 10000
TEXT_DISCOVERY_MSG = b"4f3d-FND"
DISCOVERY_TIMEOUT = 2.0


def _get_subnets() -> list[tuple[str, str]]:
    """获取所有非回环网卡的 (IP, 广播地址) 列表，使用 ip addr 命令"""
    subnets = []
    try:
        import subprocess
        out = subprocess.check_output(
            ['ip', '-4', '-o', 'addr', 'show'], text=True, timeout=3)
        for line in out.strip().split('\n'):
            parts = line.split()
            if len(parts) < 4:
                continue
            iface = parts[1]
            if iface == 'lo':
                continue
            for i, p in enumerate(parts):
                if p == 'inet' and i + 1 < len(parts):
                    cidr = parts[i + 1]
                    ip_str = cidr.split('/')[0]
                    brd = None
                    if 'brd' in parts:
                        brd_idx = parts.index('brd')
                        if brd_idx + 1 < len(parts):
                            brd = parts[brd_idx + 1]
                    if not brd:
                        octets = ip_str.split('.')
                        brd = f"{octets[0]}.{octets[1]}.{octets[2]}.255"
                    subnets.append((ip_str, brd))
                    break
    except Exception as e:
        logger.debug("[Discovery] ip addr 获取失败: %s", e)

    if not subnets:
        subnets = [('0.0.0.0', '192.168.0.255'), ('0.0.0.0', '192.168.1.255')]
    return subnets


class WMaxDiscovery:
    """UDP 发现局域网内的 WMax 设备"""

    async def discover(self, timeout: float = DISCOVERY_TIMEOUT,
                       bind_ip: str = "0.0.0.0") -> list[dict]:
        logger.info("[Discovery] 开始设备发现: timeout=%.1fs bind=%s", timeout, bind_ip)

        loop = asyncio.get_event_loop()
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wmax-disc")

        try:
            text_future = loop.run_in_executor(
                executor, self._discover_text, bind_ip, timeout)
            bin_future = loop.run_in_executor(
                executor, self._discover_binary, bind_ip, timeout)

            text_devices, bin_devices = await asyncio.gather(
                text_future, bin_future, return_exceptions=True)

            if isinstance(text_devices, Exception):
                logger.warning("[Discovery] 文本发现异常: %s", text_devices)
                text_devices = []
            if isinstance(bin_devices, Exception):
                logger.warning("[Discovery] 二进制发现异常: %s", bin_devices)
                bin_devices = {}

        finally:
            executor.shutdown(wait=False)

        result = list(bin_devices.values()) if isinstance(bin_devices, dict) else []

        if text_devices and not result:
            for td in text_devices:
                parts = td.split(":", 1)
                if parts:
                    result.append({"source_ip": parts[0].strip(),
                                   "text_info": td})

        logger.info("[Discovery] 发现完成: %d 台设备", len(result))
        return result

    def _discover_text(self, bind_ip: str, timeout: float) -> list[str]:
        text_devices = []
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(timeout)
            sock.bind((bind_ip, 0))

            logger.debug("[Discovery] 发送文本发现 → 255.255.255.255:%d", TEXT_DISCOVERY_PORT)
            sock.sendto(TEXT_DISCOVERY_MSG, ("255.255.255.255", TEXT_DISCOVERY_PORT))

            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    sock.settimeout(max(0.1, deadline - time.time()))
                    data, addr = sock.recvfrom(4096)
                    text = data.decode("utf-8", errors="replace")
                    logger.debug("[Discovery] 文本响应: %s → %s", addr[0], text[:100])
                    text_devices.append(f"{addr[0]}: {text}")
                except socket.timeout:
                    break
                except OSError:
                    break
        except Exception as e:
            logger.warning("[Discovery] 文本发现失败: %s", e)
        finally:
            if sock:
                sock.close()
        return text_devices

    def _discover_binary(self, bind_ip: str, timeout: float) -> dict[str, dict]:
        devices: dict[str, dict] = {}
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(timeout)
            sock.bind((bind_ip, 0))

            find_frame = pack(Command(cmd_type=CmdType.FindDevice))

            subnets = _get_subnets()
            logger.debug("[Discovery] 检测到子网: %s", subnets)

            targets = set(['255.255.255.255'])
            for ip_addr, brd in subnets:
                targets.add(brd)
                octets = ip_addr.split('.')
                if len(octets) == 4:
                    prefix = f"{octets[0]}.{octets[1]}.{octets[2]}"
                    for last in [1, 100, 200, 50, 150, 2, 10, 254]:
                        targets.add(f"{prefix}.{last}")

            for target in targets:
                try:
                    sock.sendto(find_frame, (target, UDP_DISCOVERY_PORT))
                except OSError:
                    pass
            logger.debug("[Discovery] FindDevice 发送至 %d 个目标", len(targets))

            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    sock.settimeout(max(0.1, deadline - time.time()))
                    data, addr = sock.recvfrom(65536)
                    logger.debug("[Discovery] 响应: %s:%d %dB", addr[0], addr[1], len(data))
                    receiver = DataReceiver()
                    cmds = receiver.feed(data)
                    for cmd in cmds:
                        if cmd.is_response and cmd.cmd_type == CmdType.FindDevice and cmd.data_part:
                            self._parse_device(cmd, addr[0], devices)
                except socket.timeout:
                    break
                except OSError as e:
                    logger.warning("[Discovery] 接收错误: %s", e)
                    break
        except Exception as e:
            logger.warning("[Discovery] 二进制发现失败: %s", e)
        finally:
            if sock:
                sock.close()

        return devices

    def _parse_device(self, cmd: Command, source_ip: str, devices: dict):
        try:
            info = decode_find_device_resp(cmd.data_part)
            sn = cmd.serial_number or info.get("dev_info", {}).get("sn") or source_ip
            info["source_ip"] = source_ip
            info["source_port"] = UDP_DISCOVERY_PORT
            info["serial_number"] = sn
            devices[sn] = info
            logger.info("[Discovery] 发现设备: %s (sn=%s, name=%s)",
                        source_ip, sn,
                        info.get("dev_info", {}).get("dev_name", "?"))
        except Exception as e:
            logger.warning("[Discovery] 解析 FindDeviceResp 失败: %s (来自 %s)", e, source_ip)
