"""
外部 MES 适配器注册表

根据 MESConnection.adapter_type 字段选择对应适配器。
"""
from backend.services.mes_adapters.base import BaseAdapter
from backend.services.mes_adapters.rest_adapter import RESTAdapter
from backend.services.mes_adapters.form_data_adapter import FormDataAdapter
from backend.services.mes_adapters.modbus_adapter import ModbusRTUAdapter

_REGISTRY: dict[str, type[BaseAdapter]] = {
    "rest": RESTAdapter,
    "form-data": FormDataAdapter,
    "modbus_rtu": ModbusRTUAdapter,
}


def get_adapter(adapter_type: str) -> BaseAdapter:
    cls = _REGISTRY.get(adapter_type)
    if not cls:
        raise ValueError(f"未知的适配器类型: {adapter_type}, 可用: {list(_REGISTRY.keys())}")
    return cls()


def register_adapter(name: str, cls: type[BaseAdapter]):
    _REGISTRY[name] = cls
