"""短信发送 Provider 实现与统一工厂。

五个正式通道 (at_modem / generic_http / wxpusher / aliyun / tencent)
+ 一个测试通道 (mock, 不可持久化)。NG 短信通知 (sms_service) 与
每日短信日报 (sms_report) 共用这一层, 不允许再各自维护通道实现。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from backend.services.sms_providers.aliyun_provider import AliyunProvider
from backend.services.sms_providers.at_modem_provider import AtModemProvider
from backend.services.sms_providers.base_provider import (
    RecipientResult,
    SmsBatchResult,
    SmsProvider,
)
from backend.services.sms_providers.generic_http_provider import GenericHttpProvider
from backend.services.sms_providers.mock_provider import MockProvider
from backend.services.sms_providers.tencent_provider import TencentProvider
from backend.services.sms_providers.wxpusher_provider import WxpusherProvider


# 通道注册表: 一处登记, 双功能共用。at_modem 需要 modem_factory, 单独分支。
_HTTP_PROVIDERS: dict[str, type[SmsProvider]] = {
    "generic_http": GenericHttpProvider,
    "wxpusher": WxpusherProvider,
    "aliyun": AliyunProvider,
    "tencent": TencentProvider,
    "mock": MockProvider,
}

# 前端下拉/校验用的正式通道清单 (mock 不在内)
PROVIDER_LABELS: dict[str, str] = {
    "at_modem": "USB 4G 短信猫 (AT)",
    "generic_http": "通用 HTTP 短信网关",
    "wxpusher": "微信推送 (WxPusher)",
    "aliyun": "阿里云短信",
    "tencent": "腾讯云短信",
}


def create_provider(
    config: Any,
    *,
    provider_name: str | None = None,
    request_func: Callable[..., object] | None = None,
    logger: Callable[[str], None] | None = None,
    stop_event: threading.Event | None = None,
    modem_factory: Callable[[], object] | None = None,
) -> SmsProvider:
    """按名字构造 Provider 实例。

    ``provider_name`` 缺省取 ``config.provider``; ``mock`` 只能显式点名。
    ``at_modem`` 必须提供 ``modem_factory`` (串口构造留给调用方注入)。
    """

    name = (provider_name or getattr(config, "provider", "") or "").strip()
    if name == "at_modem":
        if modem_factory is None:
            raise ValueError("at_modem 通道必须注入 modem_factory")
        return AtModemProvider(
            config,
            modem_factory=modem_factory,
            logger=logger or (lambda _message: None),
            stop_event=stop_event or threading.Event(),
        )
    cls = _HTTP_PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"不支持的短信 Provider：{name}")
    return cls(
        config,
        request_func=request_func,
        logger=logger,
        stop_event=stop_event,
    )


__all__ = [
    "AliyunProvider",
    "AtModemProvider",
    "GenericHttpProvider",
    "MockProvider",
    "PROVIDER_LABELS",
    "RecipientResult",
    "SmsBatchResult",
    "SmsProvider",
    "TencentProvider",
    "WxpusherProvider",
    "create_provider",
]
