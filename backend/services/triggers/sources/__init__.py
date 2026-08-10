"""触发源类型注册表 (对照 plc/drivers 形态)。

内建类型惰性 import (hid_key/serial_pattern 依赖库缺失只影响对应类型);
插件经 register_trigger_source() 注册客户怪癖触发器, 覆盖同名即定制。
"""
import importlib
from typing import Dict, List, Optional, Type

from backend.services.triggers.sources.base import BaseTriggerSource

# type 名 → (模块名, 类名, 说明, 依赖库 import 名 (None=纯内置))
_BUILTIN: Dict[str, tuple] = {
    "pixel_region": ("backend.services.triggers.sources.pixel_region",
                     "PixelRegionSource", "画面标定区像素差分 (虚拟按钮/指示灯)", None),
    "hid_key": ("backend.services.triggers.sources.hid_key",
                "HidKeySource", "HID 按键 (脚踏板/按钮盒/遥控器)", "pynput"),
    "http": ("backend.services.triggers.sources.http_source",
             "HttpSource", "外部系统调 URL 触发", None),
    "serial_pattern": ("backend.services.triggers.sources.serial_pattern",
                       "SerialPatternSource", "串口报文正则匹配", "serial"),
    "timer": ("backend.services.triggers.sources.timer_source",
              "TimerSource", "定时/每日定点", None),
    "mock": ("backend.services.triggers.sources.mock",
             "MockTriggerSource", "联调/测试注入", None),
}

_PLUGIN: Dict[str, Type[BaseTriggerSource]] = {}


def register_trigger_source(name: str, cls: Type[BaseTriggerSource]):
    """插件注册触发源类型 (hook: trigger_source)。覆盖内建同名即定制。"""
    _PLUGIN[name] = cls


def get_source_class(name: str) -> Type[BaseTriggerSource]:
    if name in _PLUGIN:
        return _PLUGIN[name]
    entry = _BUILTIN.get(name)
    if entry is None:
        raise ValueError(f"未知触发源类型: {name}")
    module_name, cls_name, hint, lib = entry
    try:
        if lib:
            importlib.import_module(lib)
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise RuntimeError(f"触发源 {name} 依赖库未安装 ({hint}): {e}") from e
    return getattr(module, cls_name)


def validate_source_params(type_name: str, params: dict) -> Optional[str]:
    """API 保存前校验 (依赖库缺失也放行保存, 启用时才报)。"""
    try:
        cls = get_source_class(type_name)
    except (ValueError, RuntimeError) as e:
        if isinstance(e, ValueError):
            return str(e)
        return None  # 依赖缺失: 允许保存配置, 启用时引擎标 error
    return cls.validate_params(params or {})


def list_source_types() -> List[dict]:
    result = []
    for name, (module_name, cls_name, hint, lib) in _BUILTIN.items():
        available, error = True, None
        try:
            if lib:
                importlib.import_module(lib)
            importlib.import_module(module_name)
        except Exception as e:
            available, error = False, str(e)
        result.append({"name": name, "hint": hint, "builtin": True,
                       "available": available, "error": error})
    for name, cls in _PLUGIN.items():
        result.append({"name": name,
                       "hint": getattr(cls, "hint", "插件注册"),
                       "builtin": False, "available": True, "error": None})
    return result
