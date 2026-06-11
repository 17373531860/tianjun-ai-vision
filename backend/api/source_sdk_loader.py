"""海康设备 SDK 加载器 + 调试日志开关。

集中处理：
  - HCNetSDK (NVR / 网络硬盘录像机) 的 wrapper 加载
  - 海康工业相机 (MvCamera) 的 SDK 加载和符号绑定
  - HIK_SDK_AVAILABLE / HCNET_SDK_AVAILABLE 全局可用性标志
  - debug_log / hik_log 调试输出
  - _decode_hik_string / get_hikvision_device_list 工具函数

为什么独立：
  原本这些 module-level 符号都散在 source.py 顶部，导致后续 source_industrial_camera_mixin
  无法干净 import（要么循环依赖，要么写一堆延迟绑定）。把 SDK loader 抽出来后，
  mixin 直接 `from backend.api.source_sdk_loader import MvCamera, ...` 即可。

source.py 仍然 `from backend.api.source_sdk_loader import *` 做向后兼容 re-export，
这样 source_routes.py / 旧代码 `from backend.api.source import HIK_SDK_AVAILABLE` 等
继续可用，零迁移成本。
"""
from __future__ import annotations

import os
import sys
from ctypes import POINTER, cast
from datetime import datetime

# ========== 调试日志开关 ==========
# v3.17.x: 改由调试中心 (backend/core/debug_center.py) 统一管理 —
# 设置页「调试设置」(开发者模式) 可运行时开关, 不再需要改源码常量重启.
# 旧常量保留作兼容 shim (历史代码/热补丁可能引用), 但不再是判定主路径.
HIK_DEBUG = False     # 兼容 shim: 真实开关 = debug_center 'backend.hik'
FREEZE_DEBUG = False  # 兼容 shim: 真实开关 = debug_center 'backend.detection'
                      # (原默认 True 致终端被采集/推理日志刷屏, 现默认静默)


from backend.core import debug_center


def debug_log(msg, category="MAIN"):
    """检测热路径调试日志 — 由调试设置页 'backend.detection' 开关控制"""
    if debug_center.is_on("backend.detection"):
        debug_center.dbg("backend.detection", f"[{category}] {msg}")
    elif FREEZE_DEBUG:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] [DEBUG/{category}] {msg}", flush=True)


def hik_log(msg, level="INFO"):
    """海康相机调试日志 — 由调试设置页 'backend.hik' 开关控制; ERROR/WARN/SUCCESS 始终输出"""
    if debug_center.is_on("backend.hik"):
        debug_center.dbg("backend.hik", f"[{level}] {msg}")
    elif HIK_DEBUG or level in ("ERROR", "WARN", "SUCCESS"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] [海康SDK/{level}] {msg}", flush=True)


# ========== HCNetSDK (NVR / IPC 录像机) ==========
HCNET_SDK_AVAILABLE = False
HCNetSession = None
try:
    from backend.hcnetsdk.wrapper import HCNetSession as _HCNetSession
    if _HCNetSession.sdk_available():
        HCNetSession = _HCNetSession
        HCNET_SDK_AVAILABLE = True
        print("[HCNetSDK] DLL available")
    else:
        print("[HCNetSDK] DLL not found (Windows deploy only)")
except Exception as _e:
    print(f"[HCNetSDK] load failed: {_e}")


# ========== 海康工业相机 SDK (MvCamera) ==========
HIK_SDK_AVAILABLE = False
MvCamera = None
MV_CC_DEVICE_INFO_LIST = None
MV_CC_DEVICE_INFO = None
MV_FRAME_OUT_INFO_EX = None
MVCC_INTVALUE = None
MV_TRIGGER_MODE_OFF = None
MV_CC_PIXEL_CONVERT_PARAM = None
MV_USB_DEVICE = None
MV_GIGE_DEVICE = None
MV_ACCESS_Exclusive = None
PixelType_Gvsp_RGB8_Packed = None

try:
    _mv_import_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MvImport")
    _mv_lib_dir = os.path.join(_mv_import_dir, "lib")

    if _mv_import_dir not in sys.path:
        sys.path.insert(0, _mv_import_dir)

    from backend.api.MvImport.MvCameraControl_class import MvCamera  # noqa: F401
    from backend.api.MvImport.CameraParams_header import (  # noqa: F401
        MV_CC_DEVICE_INFO_LIST,
        MV_CC_DEVICE_INFO,
        MV_FRAME_OUT_INFO_EX,
        MVCC_INTVALUE,
        MV_TRIGGER_MODE_OFF,
        MV_CC_PIXEL_CONVERT_PARAM,
    )
    from backend.api.MvImport.CameraParams_const import (  # noqa: F401
        MV_USB_DEVICE,
        MV_GIGE_DEVICE,
        MV_ACCESS_Exclusive,
    )
    from backend.api.MvImport.PixelType_header import (  # noqa: F401
        PixelType_Gvsp_RGB8_Packed,
    )

    HIK_SDK_AVAILABLE = True

except Exception:
    pass

# 这些符号是 mixin 用的"公共 SDK 接口"，逐个引用一次让 pyflakes 不再警告
_HIK_PUBLIC_REFS = (
    MvCamera, MV_CC_DEVICE_INFO_LIST, MV_CC_DEVICE_INFO, MV_FRAME_OUT_INFO_EX,
    MVCC_INTVALUE, MV_TRIGGER_MODE_OFF, MV_CC_PIXEL_CONVERT_PARAM,
    MV_USB_DEVICE, MV_GIGE_DEVICE, MV_ACCESS_Exclusive, PixelType_Gvsp_RGB8_Packed,
)
del _HIK_PUBLIC_REFS


def _decode_hik_string(ctypes_char_array):
    """从 ctypes 字符数组解码为字符串，用于海康设备名称/序列号"""
    if ctypes_char_array is None:
        return ""
    try:
        byte_str = memoryview(ctypes_char_array).tobytes()
        null_index = byte_str.find(b'\x00')
        if null_index != -1:
            byte_str = byte_str[:null_index]
        for enc in ('utf-8', 'gbk', 'latin-1'):
            try:
                return byte_str.decode(enc).strip()
            except UnicodeDecodeError:
                continue
        return byte_str.decode('latin-1', errors='replace').strip()
    except Exception:
        return ""


def get_hikvision_device_list():
    """
    枚举当前可用的海康工业相机（USB + GigE），返回 [{"index": 设备索引, "name": 显示名称}, ...]
    未安装 SDK 或枚举失败时返回空列表
    """
    if not HIK_SDK_AVAILABLE:
        return []
    try:
        device_list = MV_CC_DEVICE_INFO_LIST()
        tlayer_type = MV_USB_DEVICE | MV_GIGE_DEVICE
        ret = MvCamera.MV_CC_EnumDevices(tlayer_type, device_list)
        if ret != 0 or device_list.nDeviceNum == 0:
            return []

        result = []
        for i in range(device_list.nDeviceNum):
            st_dev = cast(device_list.pDeviceInfo[i], POINTER(MV_CC_DEVICE_INFO)).contents
            name = ""
            if st_dev.nTLayerType == MV_USB_DEVICE:
                model = _decode_hik_string(st_dev.SpecialInfo.stUsb3VInfo.chModelName)
                serial = _decode_hik_string(st_dev.SpecialInfo.stUsb3VInfo.chSerialNumber)
                name = f"USB [{model}] {serial}" if serial else f"USB [{model}]"
            elif st_dev.nTLayerType == MV_GIGE_DEVICE:
                model = _decode_hik_string(st_dev.SpecialInfo.stGigEInfo.chModelName)
                ip = st_dev.SpecialInfo.stGigEInfo.nCurrentIp
                ip_str = "%d.%d.%d.%d" % (
                    (ip >> 24) & 0xff, (ip >> 16) & 0xff,
                    (ip >> 8) & 0xff, ip & 0xff
                )
                name = f"GigE [{model}] {ip_str}"
            if not name.strip():
                name = f"海康相机 {i}"
            result.append({"index": i, "name": name})
        return result
    except Exception as e:
        hik_log(f"枚举设备失败: {e}", "ERROR")
        return []
