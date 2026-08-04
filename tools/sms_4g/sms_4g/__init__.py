"""USB 4G 短信测试工具的公共能力面。

这是多客户可复用的可选短信通知基础设施，一期独立运行，禁止依赖视觉检测主程序。
二期产品接入时通过 ``SmsService.send_alarm`` 门面异步入队，不在检测线程同步发短信。
"""

from .sms_service import (
    AlarmQueueReceipt,
    SmsBatchResult,
    SmsService,
    SmsServiceConfig,
)

__all__ = [
    "AlarmQueueReceipt",
    "SmsBatchResult",
    "SmsService",
    "SmsServiceConfig",
]
