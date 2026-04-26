"""
WMax 设备管理器 — 全局单例，管理多台 WMax 设备的生命周期
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import traceback
from typing import Optional

from .device import WMaxDevice, DEFAULT_PORT
from .discovery import WMaxDiscovery

logger = logging.getLogger(__name__)

_instance: Optional[WMaxDeviceManager] = None
_lock = threading.Lock()

def _get_known_devices_path() -> str:
    try:
        from backend.core.config import DATA_DIR
        return os.path.join(DATA_DIR, 'data', 'wmax_known_devices.json')
    except ImportError:
        return os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'wmax_known_devices.json')


def get_wmax_manager() -> WMaxDeviceManager:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                logger.info("[WMaxMgr] 创建全局 WMaxDeviceManager 单例")
                _instance = WMaxDeviceManager()
    return _instance


class WMaxDeviceManager:
    """管理所有已连接的 WMax 设备"""

    def __init__(self):
        self._devices: dict[str, WMaxDevice] = {}  # key = ip:port
        self._discovery = WMaxDiscovery()
        self._discovered_devices: list[dict] = []
        self._known_devices: list[dict] = self._load_known_devices()
        logger.info("[WMaxMgr] 初始化完成, 已知设备 %d 台", len(self._known_devices))

    def _load_known_devices(self) -> list[dict]:
        path = _get_known_devices_path()
        try:
            if os.path.exists(path):
                with open(path, 'r') as f:
                    data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("[WMaxMgr] 读取已知设备文件失败: %s", e)
        return []

    def _save_known_devices(self):
        path = _get_known_devices_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                json.dump(self._known_devices, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("[WMaxMgr] 保存已知设备文件失败: %s", e)

    def _remember_device(self, ip: str, port: int, sn: str = "", name: str = ""):
        for d in self._known_devices:
            if d["ip"] == ip and d["port"] == port:
                if sn:
                    d["sn"] = sn
                if name:
                    d["name"] = name
                self._save_known_devices()
                return
        self._known_devices.append({"ip": ip, "port": port, "sn": sn, "name": name})
        self._save_known_devices()
        logger.info("[WMaxMgr] 记住新设备 %s:%d (%s)", ip, port, name or sn)

    def _forget_device(self, ip: str, port: int):
        self._known_devices = [d for d in self._known_devices
                               if not (d["ip"] == ip and d["port"] == port)]
        self._save_known_devices()

    def get_known_devices(self) -> list[dict]:
        return list(self._known_devices)

    def _key(self, ip: str, port: int = DEFAULT_PORT) -> str:
        return f"{ip}:{port}"

    # ── 设备发现 ─────────────────────────────────────────

    async def discover_devices(self, timeout: float = 2.0) -> list[dict]:
        logger.info("[WMaxMgr] 开始 UDP 发现设备 (timeout=%.1fs)", timeout)
        try:
            result = await self._discovery.discover(timeout=timeout)
            logger.info("[WMaxMgr] 发现 %d 台设备", len(result))
            for d in result:
                logger.debug("[WMaxMgr]   → %s", d.get("source_ip", d))
            return result
        except Exception as e:
            logger.error("[WMaxMgr] 设备发现异常: %s\n%s", e, traceback.format_exc())
            return []

    def _register_scan_callback(self, dev: WMaxDevice):
        """注册扫码回调，将 RPT 端口扫码结果转发到 ScannerService 的扫码记录系统"""
        def _on_code(code_info: dict):
            try:
                from backend.services.scanner import get_scanner_service
                svc = get_scanner_service()
                codes = code_info.get("codes", [])
                for code in codes:
                    barcode = code.get("data", "")
                    if barcode:
                        svc.inject_scan_result(dev.ip, barcode)
            except Exception as e:
                logger.debug("[WMaxMgr] 扫码回调转发异常: %s", e)
        dev.on_code_received = _on_code

    # ── 连接管理 ─────────────────────────────────────────

    def connect(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        key = self._key(ip, port)
        logger.info("[WMaxMgr] 连接设备 %s", key)

        if key in self._devices:
            dev = self._devices[key]
            if dev.state.connected:
                logger.info("[WMaxMgr] %s 已连接，跳过", key)
                return {"success": True, "message": "已连接", "status": dev.get_status()}
            logger.info("[WMaxMgr] %s 已存在但断开，先清理", key)
            dev.disconnect()

        dev = WMaxDevice(ip, port)
        ok = dev.connect()
        if ok:
            self._devices[key] = dev
            self._register_scan_callback(dev)
            self._remember_device(ip, port, sn=dev.info.sn, name=dev.info.name)
            logger.info("[WMaxMgr] %s 连接成功，当前管理 %d 台设备",
                        key, len(self._devices))
            return {"success": True, "message": "连接成功", "status": dev.get_status()}

        logger.error("[WMaxMgr] %s 连接失败: %s", key, dev.state.last_error)
        return {"success": False, "message": f"连接失败: {dev.state.last_error}"}

    def disconnect(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        key = self._key(ip, port)
        logger.info("[WMaxMgr] 断开设备 %s", key)
        dev = self._devices.pop(key, None)
        if dev:
            dev.disconnect()
            logger.info("[WMaxMgr] %s 已断开，剩余 %d 台", key, len(self._devices))
            return {"success": True, "message": "已断开"}
        logger.warning("[WMaxMgr] %s 未找到", key)
        return {"success": False, "message": "设备未找到"}

    def get_device(self, ip: str, port: int = DEFAULT_PORT) -> Optional[WMaxDevice]:
        key = self._key(ip, port)
        dev = self._devices.get(key)
        if dev and dev.state.connected:
            return dev
        if dev and not dev.state.connected:
            logger.warning("[WMaxMgr] %s 存在但已断开", key)
        return None

    def get_all_status(self) -> list[dict]:
        result = []
        for key, dev in self._devices.items():
            result.append(dev.get_status())
        logger.debug("[WMaxMgr] 查询所有状态: %d 台设备", len(result))
        return result

    # ── 参数操作（异步包装） ──────────────────────────────

    async def handshake(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            logger.warning("[WMaxMgr] handshake: %s:%d 未连接", ip, port)
            return {"error": "设备未连接"}
        return await dev.handshake()

    async def load_config(self, ip: str, port: int = DEFAULT_PORT,
                          config_id: int = -1) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            logger.warning("[WMaxMgr] load_config: %s:%d 未连接", ip, port)
            return {"error": "设备未连接"}
        return await dev.load_config(config_id)

    async def get_device_features(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            logger.warning("[WMaxMgr] get_device_features: %s:%d 未连接", ip, port)
            return {"error": "设备未连接"}
        return await dev.get_device_features()

    async def set_params(self, ip: str, port: int = DEFAULT_PORT, **kwargs) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            logger.warning("[WMaxMgr] set_params: %s:%d 未连接", ip, port)
            return {"error": "设备未连接"}
        param_types = [k for k, v in kwargs.items() if v is not None]
        logger.info("[WMaxMgr] set_params %s:%d → %s", ip, port, param_types)
        try:
            ok = await dev.set_params(**kwargs)
            return {"success": ok}
        except Exception as e:
            logger.error("[WMaxMgr] set_params 异常: %s\n%s", e, traceback.format_exc())
            return {"error": str(e)}

    async def save_params(self, ip: str, port: int = DEFAULT_PORT, **kwargs) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            logger.warning("[WMaxMgr] save_params: %s:%d 未连接", ip, port)
            return {"error": "设备未连接"}
        param_types = [k for k, v in kwargs.items() if v is not None]
        logger.info("[WMaxMgr] save_params %s:%d → %s", ip, port, param_types)
        try:
            ok = await dev.save_config(**kwargs)
            return {"success": ok}
        except Exception as e:
            logger.error("[WMaxMgr] save_params 异常: %s\n%s", e, traceback.format_exc())
            return {"error": str(e)}

    async def auto_focus(self, ip: str, port: int = DEFAULT_PORT,
                         start: bool = True) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.info("[WMaxMgr] auto_focus %s:%d start=%s", ip, port, start)
        ok = await dev.auto_focus(start)
        return {"success": ok}

    async def start_tune(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.info("[WMaxMgr] start_tune %s:%d", ip, port)
        ok = await dev.start_tune()
        return {"success": ok}

    async def cancel_tune(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.info("[WMaxMgr] cancel_tune %s:%d", ip, port)
        ok = await dev.cancel_tune()
        return {"success": ok}

    def trigger_on(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.info("[WMaxMgr] trigger_on %s:%d", ip, port)
        dev.trigger_on()
        return {"success": True}

    def trigger_off(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.info("[WMaxMgr] trigger_off %s:%d", ip, port)
        dev.trigger_off()
        return {"success": True}

    async def turn_on_video(self, ip: str, port: int = DEFAULT_PORT,
                            on: bool = True) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.info("[WMaxMgr] turn_on_video %s:%d on=%s", ip, port, on)
        ok = await dev.turn_on_video(on)
        return {"success": ok}

    async def turn_on_trigger_image(self, ip: str, port: int = DEFAULT_PORT,
                                    on: bool = True) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.info("[WMaxMgr] turn_on_trigger_image %s:%d on=%s", ip, port, on)
        ok = await dev.turn_on_trigger_image(on)
        return {"success": ok}

    def get_image(self, ip: str, port: int = DEFAULT_PORT) -> Optional[dict]:
        dev = self.get_device(ip, port)
        if not dev:
            return None
        img = dev.get_last_image()
        if not img or not img.image_data:
            logger.debug("[WMaxMgr] get_image %s:%d 无图像", ip, port)
            return None
        logger.debug("[WMaxMgr] get_image %s:%d → %dx%d %dB",
                     ip, port, img.width, img.height, len(img.image_data))
        return {
            "width": img.width,
            "height": img.height,
            "format": img.image_format,
            "timestamp": img.timestamp,
            "data_base64": base64.b64encode(img.image_data).decode("ascii"),
            "data_len": len(img.image_data),
        }

    def get_last_code(self, ip: str, port: int = DEFAULT_PORT) -> Optional[dict]:
        dev = self.get_device(ip, port)
        if not dev:
            return None
        return dev.get_last_code()

    async def reboot(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.warning("[WMaxMgr] reboot %s:%d", ip, port)
        ok = await dev.reboot()
        return {"success": ok}

    async def reset_to_default(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.warning("[WMaxMgr] reset_to_default %s:%d", ip, port)
        ok = await dev.reset_to_default()
        return {"success": ok}

    async def indicate_device(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.info("[WMaxMgr] indicate_device %s:%d", ip, port)
        ok = await dev.indicate_device()
        return {"success": ok}

    async def set_run_mode(self, ip: str, port: int = DEFAULT_PORT,
                           mode: int = 0, bank_id: int = 0,
                           start: bool = True, image: bool = True) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.info("[WMaxMgr] set_run_mode %s:%d mode=%d bank=%d",
                    ip, port, mode, bank_id)
        ok = await dev.set_run_mode(mode, bank_id, start, image)
        return {"success": ok}

    async def start_read_rate_test(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.info("[WMaxMgr] start_read_rate_test %s:%d", ip, port)
        ok = await dev.start_read_rate_test()
        return {"success": ok}

    async def stop_read_rate_test(self, ip: str, port: int = DEFAULT_PORT) -> dict:
        dev = self.get_device(ip, port)
        if not dev:
            return {"error": "设备未连接"}
        logger.info("[WMaxMgr] stop_read_rate_test %s:%d", ip, port)
        ok = await dev.stop_read_rate_test()
        return {"success": ok}

    def get_read_rate_result(self, ip: str, port: int = DEFAULT_PORT) -> Optional[dict]:
        dev = self.get_device(ip, port)
        if not dev:
            return None
        return dev.state.read_rate_result

    def get_discovered(self) -> list[dict]:
        """返回最近一次 UDP 发现的设备列表"""
        return list(self._discovered_devices)

    async def auto_discover_and_connect(self, timeout: float = 2.0) -> list[dict]:
        """自动发现并连接所有局域网内的 WMax 设备，同时恢复已知设备"""
        logger.info("[WMaxMgr] === 自动发现+连接 开始 ===")
        results = []
        connected_ips = set()
        try:
            found = await self._discovery.discover(timeout=timeout)
            self._discovered_devices = found
            logger.info("[WMaxMgr] UDP 发现到 %d 台设备", len(found))

            for dev_info in found:
                ip = dev_info.get("source_ip")
                port = dev_info.get("source_port", DEFAULT_PORT)
                if not ip:
                    continue

                info = dev_info.get("dev_info", {})
                sn = dev_info.get("serial_number", "") or info.get("sn", "")
                name = info.get("dev_name", "") or f"WMax-{ip}"

                key = self._key(ip, port)
                if key in self._devices and self._devices[key].state.connected:
                    logger.info("[WMaxMgr] %s 已连接，跳过", key)
                    connected_ips.add(ip)
                    results.append({
                        "ip": ip, "port": port, "sn": sn, "name": name,
                        "action": "already_connected",
                    })
                    continue

                logger.info("[WMaxMgr] 自动连接 %s (%s)", key, name)
                conn_result = self.connect(ip, port)

                if conn_result.get("success"):
                    connected_ips.add(ip)
                    dev = self._devices.get(key)
                    if dev:
                        dev.info.sn = sn
                        dev.info.name = name
                        # v2.7.7c 合并: 连接后立即激活 RPT 上报 (GetConfigOpt + TurnOnOffVideo on).
                        # 与官方 IDManager 行为一致, 现场验证扫码器并不会持续闪光.
                        # 之前担心"灯一直闪"其实是误判, 而 ondemand/trigger_on 在现场 WMax
                        # 固件上根本触发不了识别, 导致扫码器完全静默.
                        try:
                            await dev.activate_rpt_reporting()
                        except Exception as e:
                            logger.warning("[WMaxMgr] %s activate_rpt 异常: %s", key, e)
                    results.append({
                        "ip": ip, "port": port, "sn": sn, "name": name,
                        "action": "connected",
                    })
                else:
                    results.append({
                        "ip": ip, "port": port, "sn": sn, "name": name,
                        "action": "failed", "error": conn_result.get("message", ""),
                    })

        except Exception as e:
            logger.error("[WMaxMgr] 自动发现+连接异常: %s\n%s", e, traceback.format_exc())

        for known in self._known_devices:
            ip = known.get("ip")
            port = known.get("port", DEFAULT_PORT)
            if not ip or ip in connected_ips:
                continue
            key = self._key(ip, port)
            if key in self._devices and self._devices[key].state.connected:
                connected_ips.add(ip)
                continue
            logger.warning("[WMaxMgr] 恢复已知设备 %s:%d (%s)", ip, port, known.get("name", ""))
            conn_result = self.connect(ip, port)
            if conn_result.get("success"):
                connected_ips.add(ip)
                dev = self._devices.get(key)
                if dev:
                    if known.get("sn"):
                        dev.info.sn = known["sn"]
                    if known.get("name"):
                        dev.info.name = known["name"]
                    # v2.7.7c 合并: 连接后激活 RPT 上报 (参见上方 UDP 发现分支的说明)
                    try:
                        await dev.activate_rpt_reporting()
                    except Exception as e:
                        logger.warning("[WMaxMgr] %s (已知) activate_rpt 异常: %s", key, e)
                results.append({
                    "ip": ip, "port": port,
                    "sn": known.get("sn", ""),
                    "name": known.get("name", f"WMax-{ip}"),
                    "action": "connected",
                })
            else:
                logger.warning("[WMaxMgr] 已知设备 %s:%d 恢复失败: %s",
                               ip, port, conn_result.get("message", ""))
                results.append({
                    "ip": ip, "port": port,
                    "sn": known.get("sn", ""),
                    "name": known.get("name", f"WMax-{ip}"),
                    "action": "failed",
                    "error": conn_result.get("message", ""),
                })

        logger.info("[WMaxMgr] === 自动发现+连接 完成: %d 台 ===", len(results))
        return results

    def shutdown(self):
        logger.info("[WMaxMgr] 关闭所有设备 (%d 台)", len(self._devices))
        for key in list(self._devices.keys()):
            dev = self._devices.pop(key)
            dev.disconnect()
        logger.info("[WMaxMgr] 所有设备已关闭")
