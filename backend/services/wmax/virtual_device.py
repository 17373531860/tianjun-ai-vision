"""
虚拟 WMax 设备 — 用于 UI 预览和功能演示

模拟真实 WMaxDevice 的所有 API，返回逼真的模拟数据，
不需要真实的 TCP 连接。
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

from .device import DeviceInfo, DeviceState, DEFAULT_PORT
from .protocol import ZImage

logger = logging.getLogger(__name__)

VIRTUAL_IP = "192.168.1.200"
VIRTUAL_SN = "WMAX-VIRTUAL-001"


class VirtualWMaxDevice:
    """模拟 WMaxDevice 的全部接口"""

    def __init__(self, ip: str = VIRTUAL_IP, port: int = DEFAULT_PORT):
        self.ip = ip
        self.port = port
        self.sn = VIRTUAL_SN

        self.info = DeviceInfo(
            sn=VIRTUAL_SN, name="WMax VS600 (虚拟)",
            dev_type=1, hardware_version="V1.0.0",
            app_version="V2.36.39",
            ip=ip, port=port, mac="00:11:22:33:44:55",
        )
        self.state = DeviceState(connected=True)
        self.state.config = self._default_config()
        self.state.features = {"raw_fields": [1, 2, 3]}
        self._video_on = False
        self._rr_running = False
        self._rr_count = 0
        logger.info("[VirtualWMax] 虚拟设备已创建: %s:%d sn=%s", ip, port, VIRTUAL_SN)

    @staticmethod
    def _default_config() -> dict:
        return {
            "raw_fields": [1],
            "config_id": 0,
            "config_name": "默认配置",
            "sensor_opt": {
                "exposure_mode": 0, "exposure": 3000,
                "min_exposure": 10, "max_exposure": 50000,
                "gain": 2.0, "min_gain": 1.0, "max_gain": 16.0,
                "gamma": 50, "contrast": 50, "contrast_adj": 0,
                "aimer_ctrl": 2, "focus_mode": 0, "focus_value": 300,
                "frame_rate": 30,
            },
            "light_opt": {"internal_light": 60, "external_light": 0},
            "common_opt": {
                "name": "Bank0", "enable": True, "try_count": 5,
                "decode_timeout": 3000, "shutter_delay": 0,
                "inverse_read": False, "reverse_read": False,
                "base_tilt_angle": 0, "tilt_angle_range": 45,
                "decoder_type": 0, "barcode_polarity": 0, "barcode_mirror": 0,
            },
            "code_opt": {
                "codes": [
                    {"code_type": 1, "code_type_name": "QR", "enable": True, "min_len": 1, "max_len": 256},
                    {"code_type": 2, "code_type_name": "DataMatrix", "enable": True, "min_len": 1, "max_len": 256},
                    {"code_type": 3, "code_type_name": "PDF417", "enable": False, "min_len": 1, "max_len": 256},
                    {"code_type": 4, "code_type_name": "Code128", "enable": True, "min_len": 1, "max_len": 64},
                    {"code_type": 5, "code_type_name": "Code39", "enable": True, "min_len": 1, "max_len": 64},
                    {"code_type": 6, "code_type_name": "Code93", "enable": False, "min_len": 1, "max_len": 64},
                    {"code_type": 7, "code_type_name": "Codabar", "enable": False, "min_len": 1, "max_len": 64},
                    {"code_type": 8, "code_type_name": "ITF", "enable": False, "min_len": 1, "max_len": 64},
                    {"code_type": 9, "code_type_name": "Industrial2of5", "enable": False, "min_len": 1, "max_len": 64},
                    {"code_type": 10, "code_type_name": "UPC_A", "enable": True, "min_len": 12, "max_len": 12},
                    {"code_type": 11, "code_type_name": "UPC_E", "enable": True, "min_len": 8, "max_len": 8},
                    {"code_type": 12, "code_type_name": "EAN13", "enable": True, "min_len": 13, "max_len": 13},
                    {"code_type": 13, "code_type_name": "EAN8", "enable": True, "min_len": 8, "max_len": 8},
                    {"code_type": 14, "code_type_name": "GS1Databar", "enable": False, "min_len": 1, "max_len": 64},
                    {"code_type": 15, "code_type_name": "GS1DatabarExpanded", "enable": False, "min_len": 1, "max_len": 128},
                    {"code_type": 17, "code_type_name": "DotCode", "enable": False, "min_len": 1, "max_len": 256},
                ],
                "output_len_limit": False, "output_len": 32,
                "code_mode": 0, "start_offset": 0, "redundant": 1,
            },
            "reading_opt": {
                "enable_smart_mode": False, "polarization": 0,
                "focus_value": 300, "illum_value": 60,
                "enable_detect_roi": False, "rois": [],
                "detect_roi": {"id": 0, "rect": {"x": 100, "y": 100, "w": 440, "h": 280}},
                "img_capture_rect": {"x": 0, "y": 0, "w": 640, "h": 480},
            },
            "data_edit_opt": {
                "prefix": "", "suffix": "\\r\\n",
                "no_barcode_flags": 0, "no_barcode_str": "NOREAD",
                "barcode_number": 1,
                "multi_data": {
                    "enable": False, "delimiter_flags": 0,
                    "delimiter_str": ",", "incomplete_flags": 0,
                    "incomplete_str": "",
                },
            },
            "input_opt": {"polarity": 0, "debounce_time": 20},
            "output_opt": {"duration_time": 200, "enable_busy": False, "use_script": False},
        }

    def connect(self) -> bool:
        logger.info("[VirtualWMax] connect()")
        self.state.connected = True
        return True

    def disconnect(self):
        logger.info("[VirtualWMax] disconnect()")
        self.state.connected = False

    async def handshake(self) -> dict:
        logger.info("[VirtualWMax] handshake() → 模拟成功")
        return {"response": {"ret_code": 1}, "protocol_version": 3}

    async def load_config(self, config_id: int = -1) -> dict:
        logger.info("[VirtualWMax] load_config(config_id=%d) → 返回默认配置", config_id)
        return self.state.config

    async def get_device_features(self) -> dict:
        logger.info("[VirtualWMax] get_device_features()")
        return self.state.features

    async def set_params(self, **kwargs) -> bool:
        applied = [k for k, v in kwargs.items() if v is not None]
        logger.info("[VirtualWMax] set_params(%s)", ", ".join(applied))
        cfg = self.state.config
        for key in ("sensor_params", "light_params", "common_params",
                     "code_params", "reading_params", "data_edit_params",
                     "input_params", "output_params"):
            val = kwargs.get(key)
            if val:
                cfg_key = key.replace("_params", "_opt")
                if cfg_key in cfg and isinstance(cfg[cfg_key], dict):
                    cfg[cfg_key].update(val)
                    logger.debug("[VirtualWMax]   %s → 更新 %d 个字段", cfg_key, len(val))
        return True

    async def save_config(self, **kwargs) -> bool:
        logger.info("[VirtualWMax] save_config()")
        await self.set_params(**kwargs)
        return True

    async def turn_on_video(self, on: bool = True) -> bool:
        logger.info("[VirtualWMax] turn_on_video(%s)", on)
        self._video_on = on
        return True

    async def auto_focus(self, start: bool = True) -> bool:
        logger.info("[VirtualWMax] auto_focus(%s)", start)
        return True

    async def start_tune(self) -> bool:
        logger.info("[VirtualWMax] start_tune()")
        return True

    async def cancel_tune(self) -> bool:
        logger.info("[VirtualWMax] cancel_tune()")
        return True

    def trigger_on(self):
        code = f"DEMO-{random.randint(10000, 99999)}"
        logger.info("[VirtualWMax] trigger_on() → 模拟码 %s", code)
        self.state.last_code = {
            "codes": [{"data": code, "code_type": 1, "score": 95}],
            "img_timestamp": int(time.time() * 1000),
        }

    def trigger_off(self):
        logger.info("[VirtualWMax] trigger_off()")

    async def reboot(self) -> bool:
        logger.info("[VirtualWMax] reboot() → 模拟重启")
        return True

    async def reset_to_default(self, **kwargs) -> bool:
        logger.info("[VirtualWMax] reset_to_default()")
        self.state.config = self._default_config()
        return True

    async def indicate_device(self) -> bool:
        logger.info("[VirtualWMax] indicate_device()")
        return True

    async def set_run_mode(self, mode=0, bank_id=0, start=True, image=True) -> bool:
        logger.info("[VirtualWMax] set_run_mode(mode=%d, bank=%d)", mode, bank_id)
        return True

    async def turn_on_trigger_image(self, on: bool = True) -> bool:
        logger.info("[VirtualWMax] turn_on_trigger_image(%s)", on)
        return True

    async def start_read_rate_test(self) -> bool:
        logger.info("[VirtualWMax] start_read_rate_test()")
        self._rr_running = True
        self._rr_count = 0
        self.state.read_rate_result = {
            "total_count": 0, "success_count": 0, "fail_count": 0,
            "rate": 0.0, "is_running": True,
        }
        return True

    async def stop_read_rate_test(self) -> bool:
        logger.info("[VirtualWMax] stop_read_rate_test() total=%d", self._rr_count)
        self._rr_running = False
        if self.state.read_rate_result:
            self.state.read_rate_result["is_running"] = False
        return True

    def get_last_image(self) -> Optional[ZImage]:
        return None

    def get_last_code(self) -> Optional[dict]:
        if self._rr_running:
            self._rr_count += 1
            total = self._rr_count
            ok = int(total * 0.95) if total > 5 else total - 1
            fail = total - ok
            self.state.read_rate_result = {
                "total_count": total, "success_count": ok,
                "fail_count": fail,
                "rate": ok / total if total else 0,
                "is_running": True,
            }
        return self.state.last_code

    def get_status(self) -> dict:
        return {
            "connected": self.state.connected,
            "ip": self.ip,
            "port": self.port,
            "sn": self.info.sn,
            "name": self.info.name,
            "last_error": "",
            "has_config": True,
            "virtual": True,
        }
