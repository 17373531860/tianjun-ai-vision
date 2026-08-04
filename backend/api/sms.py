"""滚动 12 小时生产汇总短信的多 Provider 独立配置 API。"""

from __future__ import annotations

import logging
import threading

import serial.tools.list_ports
from fastapi import APIRouter, Depends, HTTPException, Response

from backend.core.auth_deps import require_perm
from backend.schemas.sms import (
    SmsConfigPayload,
    SmsConfigResponse,
    SmsPortInfo,
    SmsPortListResponse,
    SmsQueueReceiptResponse,
    SmsTestPayload,
)
from backend.services.sms_config import SmsConfigError, SmsConfigStore
from backend.services.sms_service import SmsService, SmsServiceConfig


logger = logging.getLogger(__name__)
router = APIRouter()

_runtime_lock = threading.RLock()
_config_store = SmsConfigStore()


def _new_sms_service(config: SmsServiceConfig) -> SmsService:
    return SmsService(
        config,
        logger=lambda msg: logger.info("[SMS] %s", msg),
        offline_queue_path=_config_store.path.with_name("sms_offline_queue.db"),
        summary_state_path=_config_store.path.with_name("sms_summary_state.json"),
    )


_sms_service = _new_sms_service(_config_store.load())


def get_sms_service() -> SmsService:
    """返回当前短信汇总与多 Provider 发送门面。"""

    with _runtime_lock:
        return _sms_service


def _replace_sms_service(config: SmsServiceConfig) -> None:
    """替换配置并续接同一汇总水位；不在请求线程打开串口或发 HTTP。"""

    global _sms_service
    replacement = _new_sms_service(config)
    with _runtime_lock:
        previous = _sms_service
        should_start_scheduler = previous.summary_scheduler_running
        _sms_service = replacement
    previous.shutdown(timeout=1.0)
    if should_start_scheduler:
        replacement.start_summary_scheduler()


@router.get(
    "/config",
    summary="读取短信配置",
    response_model=SmsConfigResponse,
)
def get_sms_config() -> SmsConfigResponse:
    """读取当前运行时短信配置；缺失或损坏配置固定回退为默认关闭。"""

    return SmsConfigResponse.from_service_config(get_sms_service().config)


@router.put(
    "/config",
    summary="保存短信配置",
    response_model=SmsConfigResponse,
    dependencies=[Depends(require_perm("alarm.edit"))],
)
def put_sms_config(payload: SmsConfigPayload) -> SmsConfigResponse:
    """原子保存独立短信配置并热替换服务；不连接 COM，也不发送短信。

    保存失败返回 500，旧配置继续生效；字段或启用条件非法由 Pydantic 返回 422。
    """

    config = payload.to_service_config()
    try:
        _config_store.save(config)
    except SmsConfigError as exc:
        logger.error("短信配置保存失败，旧配置继续生效")
        raise HTTPException(status_code=500, detail="短信配置保存失败") from exc
    _replace_sms_service(config)
    return SmsConfigResponse.from_service_config(config)


@router.get(
    "/ports",
    summary="列出短信串口",
    response_model=SmsPortListResponse,
)
def list_sms_ports() -> SmsPortListResponse:
    """列出系统当前可枚举串口；只读设备信息，不打开任何串口。"""

    ports = [
        SmsPortInfo(
            port=item.device,
            description=item.description or "未知串口设备",
            hwid=item.hwid or "",
        )
        for item in serial.tools.list_ports.comports()
    ]
    return SmsPortListResponse(ports=ports)


@router.post(
    "/test",
    summary="后台测试短信通道",
    response_model=SmsQueueReceiptResponse,
    status_code=202,
    dependencies=[Depends(require_perm("alarm.edit"))],
)
def test_sms_channel(
    payload: SmsTestPayload,
    response: Response,
) -> SmsQueueReceiptResponse:
    """后台排队当前未闭合汇总窗口快照，不在请求线程做串口/HTTP I/O。"""

    receipt = get_sms_service().queue_test_sms(
        message=payload.message,
        recipients=payload.phone_numbers,
    )
    response.status_code = int(
        receipt.status_code or (202 if receipt.accepted else 400)
    )
    return SmsQueueReceiptResponse.from_receipt(receipt)
