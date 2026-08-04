"""
短信适配器注册表 (仿 mes_adapters 模式)

- get_sms_adapter(provider) 取实例
- register_sms_adapter(name, cls) 留给插件注册自定义通道 (如客户自有短信网关)
"""
from .base import BaseSmsAdapter
from .aliyun_adapter import AliyunSmsAdapter
from .tencent_adapter import TencentSmsAdapter
from .http_relay_adapter import HttpRelaySmsAdapter
from .mock_adapter import MockSmsAdapter

_REGISTRY: dict = {
    "aliyun": AliyunSmsAdapter,
    "tencent": TencentSmsAdapter,
    "http_relay": HttpRelaySmsAdapter,
    "mock": MockSmsAdapter,
}


def get_sms_adapter(provider: str) -> BaseSmsAdapter:
    cls = _REGISTRY.get((provider or "").strip().lower())
    if cls is None:
        raise ValueError(f"未知短信服务商: {provider} (可用: {', '.join(_REGISTRY)})")
    return cls()


def register_sms_adapter(name: str, cls) -> None:
    _REGISTRY[name] = cls


def list_providers() -> list:
    return list(_REGISTRY.keys())
