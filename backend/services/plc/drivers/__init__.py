"""
PLC 驱动注册表 (对照 mes_adapters 注册表模式)。

- 内建驱动惰性 import: 库缺失只在真正启用该驱动时报错, 绝不影响主程序启动
  (AGENTS.md 错误隔离底线 + 不变量 10: 不能提前拉起重依赖)
- 插件通过 register_plc_driver() 注册私有协议驱动
"""
import importlib
import logging
from typing import Dict, List, Type

from backend.services.plc.drivers.base import BasePLCDriver

logger = logging.getLogger(__name__)

# driver 名 → (模块名, 类名, 依赖库提示, 依赖库 import 名 (None=纯内置))
_BUILTIN: Dict[str, tuple] = {
    "s7": ("backend.services.plc.drivers.s7", "S7Driver",
           "python-snap7 (西门子 S7 全系)", "snap7"),
    "modbus_tcp": ("backend.services.plc.drivers.modbus", "ModbusTcpDriver",
                   "pymodbus (Modbus TCP: 台达/汇川/信捷等)", "pymodbus"),
    "modbus_rtu": ("backend.services.plc.drivers.modbus", "ModbusRtuDriver",
                   "pymodbus + pyserial (串口 Modbus RTU)", "pymodbus"),
    "mc": ("backend.services.plc.drivers.mc", "MCDriver",
           "pymcprotocol (三菱 MC 协议 3E 帧)", "pymcprotocol"),
    "fins": ("backend.services.plc.drivers.fins", "FinsDriver",
             "内置实现 (欧姆龙 FINS/UDP)", None),
    "ethernet_ip": ("backend.services.plc.drivers.enip", "EnipDriver",
                    "pycomm3 (AB/罗克韦尔 EtherNet-IP tag 访问)", "pycomm3"),
    "opcua": ("backend.services.plc.drivers.opcua", "OpcUaDriver",
              "asyncua (OPC UA 兜底, 一切带 UA Server 的设备)", "asyncua"),
    "mock": ("backend.services.plc.drivers.mock", "MockPLCDriver",
             "内置 (虚拟 PLC: 联调/测试/演示)", None),
}

_PLUGIN_REGISTRY: Dict[str, Type[BasePLCDriver]] = {}


def get_driver_class(name: str) -> Type[BasePLCDriver]:
    """按名取驱动类。插件注册的优先 (允许插件覆盖内建实现做客户怪癖适配)。"""
    if name in _PLUGIN_REGISTRY:
        return _PLUGIN_REGISTRY[name]
    entry = _BUILTIN.get(name)
    if entry is None:
        raise ValueError(f"未知 PLC 驱动: {name}, "
                         f"可用: {sorted(set(_BUILTIN) | set(_PLUGIN_REGISTRY))}")
    module_name, cls_name, hint, lib = entry
    try:
        if lib:
            importlib.import_module(lib)
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise RuntimeError(f"PLC 驱动 {name} 依赖库未安装 ({hint}): {e}") from e
    return getattr(module, cls_name)


def register_plc_driver(name: str, cls: Type[BasePLCDriver]) -> None:
    """插件注册私有协议驱动 (plugin_system 经此挂入)。"""
    _PLUGIN_REGISTRY[name] = cls
    logger.info("[PLC] 插件注册驱动: %s -> %s", name, cls.__name__)


def list_drivers() -> List[dict]:
    """全部驱动 + 依赖可用性 (前端下拉/诊断用)。只探测 import, 不做网络 IO。"""
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
    for name, cls in _PLUGIN_REGISTRY.items():
        result.append({"name": name, "hint": f"插件驱动 ({cls.__name__})",
                       "builtin": False, "available": True, "error": None})
    return result
