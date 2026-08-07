"""短信通知多 Provider API Schema。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.services.sms_config import SmsConfigError, config_from_dict
from backend.services.sms_providers.wxpusher_provider import (
    DEFAULT_WXPUSHER_API_URL,
    DEFAULT_WXPUSHER_SUMMARY_TEMPLATE,
)
from backend.services.sms_service import (
    DEFAULT_ALARM_TEMPLATE,
    AlarmQueueReceipt,
    SmsServiceConfig,
    validate_alarm_template,
)
from backend.services.sms_utils import parse_recipients


class SmsAtModemPayload(BaseModel):
    """USB 虚拟串口 + AT 通道配置。"""

    model_config = ConfigDict(extra="forbid")

    port: str = Field("", max_length=128, description="短信模块独立 COM 口")
    baudrate: int = Field(115200, ge=300, le=4_000_000)
    encoding: Literal["auto", "gsm", "ucs2"] = "auto"
    template: str = Field(DEFAULT_ALARM_TEMPLATE, min_length=1, max_length=2_000)

    @field_validator("port")
    @classmethod
    def strip_port(cls, value: str) -> str:
        return value.strip()

    @field_validator("template")
    @classmethod
    def validate_template(cls, value: str) -> str:
        validate_alarm_template(value)
        return value


class SmsGenericHttpPayload(BaseModel):
    """厂商无关的 HTTP/HTTPS JSON 通道配置。"""

    model_config = ConfigDict(extra="forbid")

    api_url: str = Field("", max_length=2_000)
    request_method: Literal["POST", "PUT"] = "POST"
    timeout_seconds: float = Field(10.0, ge=0.5, le=120)
    token: str = Field("", max_length=8_192)
    access_key: str = Field("", max_length=2_048)
    access_secret: str = Field("", max_length=8_192)
    sign_name: str = Field("", max_length=200)
    template_id: str = Field("", max_length=500)
    verify_ssl: bool = True
    field_mapping: dict[str, str] = Field(default_factory=dict, max_length=50)

    @field_validator(
        "api_url",
        "token",
        "access_key",
        "access_secret",
        "sign_name",
        "template_id",
    )
    @classmethod
    def strip_strings(cls, value: str) -> str:
        return value.strip()


class SmsWxpusherPayload(BaseModel):
    """WxPusher 微信推送通道配置。"""

    model_config = ConfigDict(extra="forbid")

    app_token: str = Field("", max_length=512)
    uids: list[str] = Field(default_factory=list, max_length=50)
    topic_ids: list[int] = Field(default_factory=list, max_length=20)
    content_type: Literal[1, 2, 3] = 1
    summary_template: str = Field(
        DEFAULT_WXPUSHER_SUMMARY_TEMPLATE, min_length=1, max_length=500
    )
    api_url: str = Field(DEFAULT_WXPUSHER_API_URL, max_length=2_000)
    timeout_seconds: float = Field(10.0, ge=0.5, le=120)
    verify_ssl: bool = True

    @field_validator("app_token", "api_url", "summary_template")
    @classmethod
    def strip_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("uids", mode="before")
    @classmethod
    def normalize_uids(cls, value: object) -> list[str]:
        if value in (None, "", []):
            return []
        if isinstance(value, str):
            parts = [
                part.strip()
                for chunk in value.replace("；", ";").split(";")
                for part in chunk.split(",")
            ]
            return [part for part in parts if part]
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError("WxPusher UID 必须是数组或分隔字符串")

    @field_validator("summary_template")
    @classmethod
    def validate_summary_template(cls, value: str) -> str:
        validate_alarm_template(value)
        return value


class SmsAliyunPayload(BaseModel):
    """阿里云官方云短信通道配置。"""

    model_config = ConfigDict(extra="forbid")

    access_key_id: str = Field("", max_length=256)
    access_key_secret: str = Field("", max_length=512)
    sign_name: str = Field("", max_length=200, description="审核过的短信签名")
    template_code: str = Field("", max_length=100, description="审核过的模板 code")
    region: str = Field("cn-hangzhou", max_length=64)

    @field_validator(
        "access_key_id", "access_key_secret", "sign_name", "template_code", "region"
    )
    @classmethod
    def strip_strings(cls, value: str) -> str:
        return value.strip()


class SmsTencentPayload(BaseModel):
    """腾讯云官方云短信通道配置。"""

    model_config = ConfigDict(extra="forbid")

    secret_id: str = Field("", max_length=256)
    secret_key: str = Field("", max_length=512)
    sdk_app_id: str = Field("", max_length=64, description="短信应用 SdkAppId")
    sign_name: str = Field("", max_length=200, description="审核过的短信签名")
    template_id: str = Field("", max_length=100, description="审核过的模板 ID")
    region: str = Field("ap-guangzhou", max_length=64)

    @field_validator(
        "secret_id", "secret_key", "sdk_app_id", "sign_name", "template_id", "region"
    )
    @classmethod
    def strip_strings(cls, value: str) -> str:
        return value.strip()


class SmsConfigPayload(BaseModel):
    """Canonical 多通道配置，并兼容一期 Alarm 页平铺字段。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(False, description="NG 短信推送总开关，默认关闭")
    provider: Literal[
        "at_modem", "generic_http", "wxpusher", "aliyun", "tencent"
    ] = "at_modem"
    at_modem: SmsAtModemPayload = Field(default_factory=SmsAtModemPayload)
    generic_http: SmsGenericHttpPayload = Field(default_factory=SmsGenericHttpPayload)
    wxpusher: SmsWxpusherPayload = Field(default_factory=SmsWxpusherPayload)
    aliyun: SmsAliyunPayload = Field(default_factory=SmsAliyunPayload)
    tencent: SmsTencentPayload = Field(default_factory=SmsTencentPayload)
    phone_numbers: list[str] = Field(default_factory=list, max_length=20)
    retry_count: int = Field(3, ge=0, le=5)
    retry_backoff_seconds: list[float] = Field(
        default_factory=lambda: [1.0, 3.0, 5.0], min_length=1, max_length=10
    )
    ng_threshold: int = Field(
        5,
        ge=1,
        le=100,
        description="旧版即时 NG 阈值兼容字段；12 小时汇总模式不使用",
    )
    cooldown_seconds: float = Field(60.0, ge=0, le=86_400)
    queue_size: int = Field(100, ge=1, le=1_000)
    offline_queue_max: int = Field(200, ge=1, le=10_000)
    offline_ttl_seconds: float = Field(86_400, ge=60, le=30 * 86_400)
    summary_schedule_mode: Literal["rolling_12h", "daily_shift"] = Field(
        "rolling_12h",
        description="rolling_12h=滚动12小时；daily_shift=班次（默认早八到晚八）",
    )
    shift_start_hour: int = Field(8, ge=0, le=23, description="班次开始小时")
    shift_end_hour: int = Field(20, ge=0, le=23, description="班次结束小时（到点发送）")
    send_night_window: bool = Field(
        False,
        description="班次模式下是否额外发送晚班窗（结束小时～次日开始小时）",
    )
    summary_count_source: Literal["panel", "window"] = Field(
        "panel",
        description="panel=监控面板当前会话OK/NG（默认）；window=调度时间窗落库合计",
    )

    # 一期兼容字段：A′期间旧 Alarm 页仍按这些字段 GET/PUT。
    port: str = Field("", max_length=128)
    baudrate: int = Field(115200, ge=300, le=4_000_000)
    recipients: list[str] = Field(default_factory=list, max_length=20)
    template: str = Field(DEFAULT_ALARM_TEMPLATE, min_length=1, max_length=2_000)
    encoding: Literal["auto", "gsm", "ucs2"] = "auto"
    retries: int = Field(3, ge=0, le=5)
    retry_delay_seconds: float = Field(2.0, ge=0, le=300)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_payload(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        at_modem = dict(data.get("at_modem") or {})
        # 旧客户端可能只平铺 port/template；新页面只传 at_modem。
        # 两者都有时以嵌套为准，避免 GET 回显的平铺脏字段覆盖已修好的 at_modem。
        for legacy_name in ("port", "baudrate", "template", "encoding"):
            if legacy_name in data and legacy_name not in at_modem:
                at_modem[legacy_name] = data[legacy_name]
        data["at_modem"] = at_modem
        if "recipients" in data:
            data["phone_numbers"] = data["recipients"]
        elif "phone_numbers" in data:
            data["recipients"] = data["phone_numbers"]
        if "retries" in data:
            data["retry_count"] = data["retries"]
        elif "retry_count" in data:
            data["retries"] = data["retry_count"]
        if "retry_backoff_seconds" not in data and "retry_delay_seconds" in data:
            count = max(1, int(data.get("retry_count", 1)))
            data["retry_backoff_seconds"] = [data["retry_delay_seconds"]] * count
        return data

    @field_validator("phone_numbers", "recipients", mode="before")
    @classmethod
    def normalize_recipients(cls, value: object) -> list[str]:
        if value in (None, "", []):
            return []
        if not isinstance(value, (str, list, tuple)):
            raise ValueError("接收手机号必须是数组或分隔字符串")
        return list(parse_recipients(value))

    @field_validator("retry_backoff_seconds")
    @classmethod
    def validate_backoff(cls, value: list[float]) -> list[float]:
        if any(item < 0 or item > 300 for item in value):
            raise ValueError("重试退避秒数必须在 0~300 之间")
        return value

    @model_validator(mode="after")
    def validate_provider_fields(self) -> "SmsConfigPayload":
        try:
            config_from_dict(self._canonical_dict())
        except SmsConfigError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def _canonical_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "at_modem": self.at_modem.model_dump(),
            "generic_http": self.generic_http.model_dump(),
            "wxpusher": self.wxpusher.model_dump(),
            "aliyun": self.aliyun.model_dump(),
            "tencent": self.tencent.model_dump(),
            "phone_numbers": list(self.phone_numbers),
            "retry_count": self.retry_count,
            "retry_backoff_seconds": list(self.retry_backoff_seconds),
            "ng_threshold": self.ng_threshold,
            "cooldown_seconds": self.cooldown_seconds,
            "queue_size": self.queue_size,
            "offline_queue_max": self.offline_queue_max,
            "offline_ttl_seconds": self.offline_ttl_seconds,
            "summary_schedule_mode": self.summary_schedule_mode,
            "shift_start_hour": self.shift_start_hour,
            "shift_end_hour": self.shift_end_hour,
            "send_night_window": self.send_night_window,
            "summary_count_source": self.summary_count_source,
        }

    def to_service_config(self) -> SmsServiceConfig:
        """转换为已经过同一存储校验器验证的运行时配置。"""

        return config_from_dict(self._canonical_dict())

    @classmethod
    def from_service_config(cls, config: SmsServiceConfig) -> "SmsConfigPayload":
        """返回 canonical 与旧 UI 兼容字段一致的响应。"""

        return cls(
            enabled=config.enabled,
            provider=config.provider,
            at_modem=SmsAtModemPayload(
                port=config.port,
                baudrate=config.baudrate,
                encoding=config.encoding,
                template=config.template,
            ),
            generic_http=SmsGenericHttpPayload(
                api_url=config.api_url,
                request_method=config.request_method,
                timeout_seconds=config.timeout_seconds,
                token=config.token,
                access_key=config.access_key,
                access_secret=config.access_secret,
                sign_name=config.sign_name,
                template_id=config.template_id,
                verify_ssl=config.verify_ssl,
                field_mapping=dict(config.field_mapping),
            ),
            wxpusher=SmsWxpusherPayload(
                app_token=config.wxpusher_app_token,
                uids=list(config.wxpusher_uids),
                topic_ids=list(config.wxpusher_topic_ids),
                content_type=config.wxpusher_content_type,  # type: ignore[arg-type]
                summary_template=config.wxpusher_summary_template,
                api_url=config.wxpusher_api_url,
                timeout_seconds=config.wxpusher_timeout_seconds,
                verify_ssl=config.wxpusher_verify_ssl,
            ),
            aliyun=SmsAliyunPayload(
                access_key_id=config.aliyun_access_key_id,
                access_key_secret=config.aliyun_access_key_secret,
                sign_name=config.aliyun_sign_name,
                template_code=config.aliyun_template_code,
                region=config.aliyun_region,
            ),
            tencent=SmsTencentPayload(
                secret_id=config.tencent_secret_id,
                secret_key=config.tencent_secret_key,
                sdk_app_id=config.tencent_sdk_app_id,
                sign_name=config.tencent_sign_name,
                template_id=config.tencent_template_id,
                region=config.tencent_region,
            ),
            phone_numbers=list(config.recipients),
            retry_count=config.retries,
            retry_backoff_seconds=list(config.retry_backoff_seconds),
            ng_threshold=config.ng_threshold,
            cooldown_seconds=config.cooldown_seconds,
            queue_size=config.queue_size,
            offline_queue_max=config.offline_queue_max,
            offline_ttl_seconds=config.offline_ttl_seconds,
            summary_schedule_mode=config.summary_schedule_mode,
            shift_start_hour=config.shift_start_hour,
            shift_end_hour=config.shift_end_hour,
            send_night_window=config.send_night_window,
            summary_count_source=config.summary_count_source,
            port=config.port,
            baudrate=config.baudrate,
            recipients=list(config.recipients),
            template=config.template,
            encoding=config.encoding,
            retries=config.retries,
            retry_delay_seconds=(
                config.retry_backoff_seconds[0]
                if config.retry_backoff_seconds
                else config.retry_delay_seconds
            ),
        )


class SmsConfigResponse(SmsConfigPayload):
    """当前已生效的短信通知配置。"""


class SmsTestPayload(BaseModel):
    """后台测试发送请求。"""

    model_config = ConfigDict(extra="forbid")

    message: str | None = Field(
        None,
        min_length=1,
        max_length=2_000,
        description="旧调用方可自定义；缺省发送当前未闭合窗口快照",
    )
    phone_numbers: list[str] | None = Field(None, max_length=20)

    @field_validator("phone_numbers", mode="before")
    @classmethod
    def normalize_phone_numbers(cls, value: object) -> list[str] | None:
        if value in (None, "", []):
            return None
        if not isinstance(value, (str, list, tuple)):
            raise ValueError("测试手机号必须是数组或分隔字符串")
        return list(parse_recipients(value))


class SmsQueueReceiptResponse(BaseModel):
    """测试发送的即时结构化回执。"""

    success: bool
    message: str
    message_id: str
    status_code: int
    retry_count: int
    queued: bool
    error_code: str
    status: str

    @classmethod
    def from_receipt(cls, receipt: AlarmQueueReceipt) -> "SmsQueueReceiptResponse":
        return cls(
            success=bool(receipt.success),
            message=receipt.message,
            message_id=receipt.message_id,
            status_code=int(receipt.status_code or 0),
            retry_count=receipt.retry_count,
            queued=bool(receipt.queued),
            error_code=receipt.error_code,
            status=receipt.status,
        )


class SmsPortInfo(BaseModel):
    """一个可枚举串口的信息。"""

    port: str
    description: str
    hwid: str


class SmsPortListResponse(BaseModel):
    """短信模块可选串口列表。"""

    ports: list[SmsPortInfo]
