"""短信通知独立配置存储。

配置固定写入 ``DATA_DIR/sms_config.json``，不读写灯塔 ``alarm_config.json``。
磁盘使用双 Provider 的 canonical 结构；读取时兼容一期 AT 平铺结构。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from backend.core.config import DATA_DIR
from backend.services.sms_providers.generic_http_provider import DEFAULT_FIELD_MAPPING
from backend.services.sms_providers.wxpusher_provider import (
    DEFAULT_WXPUSHER_API_URL,
    DEFAULT_WXPUSHER_SUMMARY_TEMPLATE,
)
from backend.services.sms_service import SmsServiceConfig, validate_alarm_template
from backend.services.sms_summary import ALLOWED_SUMMARY_SCHEDULE_MODES
from backend.services.sms_utils import choose_encoding, parse_recipients


logger = logging.getLogger(__name__)

SMS_CONFIG_FILENAME = "sms_config.json"
ALLOWED_HTTP_METHODS = {"POST", "PUT"}
ALLOWED_PROVIDERS = {"at_modem", "generic_http", "wxpusher", "aliyun", "tencent"}
ALLOWED_MAPPING_SOURCES = set(DEFAULT_FIELD_MAPPING) | {"message", "message_id"}
ALLOWED_WXPUSHER_CONTENT_TYPES = {1, 2, 3}


class SmsConfigError(RuntimeError):
    """短信配置读取、校验或写入失败。"""


def config_to_dict(config: SmsServiceConfig) -> dict[str, Any]:
    """转换为唯一 canonical JSON；不写旧 ``port/recipients`` 平铺副本。"""

    return {
        "enabled": config.enabled,
        "provider": config.provider,
        "at_modem": {
            "port": config.port,
            "baudrate": config.baudrate,
            "encoding": config.encoding,
            "template": config.template,
        },
        "generic_http": {
            "api_url": config.api_url,
            "request_method": config.request_method,
            "timeout_seconds": config.timeout_seconds,
            "token": config.token,
            "access_key": config.access_key,
            "access_secret": config.access_secret,
            "sign_name": config.sign_name,
            "template_id": config.template_id,
            "verify_ssl": config.verify_ssl,
            "field_mapping": dict(config.field_mapping),
        },
        "wxpusher": {
            "app_token": config.wxpusher_app_token,
            "uids": list(config.wxpusher_uids),
            "topic_ids": list(config.wxpusher_topic_ids),
            "content_type": config.wxpusher_content_type,
            "summary_template": config.wxpusher_summary_template,
            "api_url": config.wxpusher_api_url,
            "timeout_seconds": config.wxpusher_timeout_seconds,
            "verify_ssl": config.wxpusher_verify_ssl,
        },
        "aliyun": {
            "access_key_id": config.aliyun_access_key_id,
            "access_key_secret": config.aliyun_access_key_secret,
            "sign_name": config.aliyun_sign_name,
            "template_code": config.aliyun_template_code,
            "region": config.aliyun_region,
        },
        "tencent": {
            "secret_id": config.tencent_secret_id,
            "secret_key": config.tencent_secret_key,
            "sdk_app_id": config.tencent_sdk_app_id,
            "sign_name": config.tencent_sign_name,
            "template_id": config.tencent_template_id,
            "region": config.tencent_region,
        },
        "phone_numbers": list(config.recipients),
        "retry_count": config.retries,
        "retry_backoff_seconds": list(config.retry_backoff_seconds),
        "ng_threshold": config.ng_threshold,
        "cooldown_seconds": config.cooldown_seconds,
        "queue_size": config.queue_size,
        "offline_queue_max": config.offline_queue_max,
        "offline_ttl_seconds": config.offline_ttl_seconds,
        "summary_schedule_mode": config.summary_schedule_mode,
        "shift_start_hour": config.shift_start_hour,
        "shift_end_hour": config.shift_end_hour,
        "send_night_window": config.send_night_window,
    }


def config_from_dict(data: dict[str, Any]) -> SmsServiceConfig:
    """读取 canonical 或一期平铺 JSON，并做 Provider 相关严格校验。"""

    if not isinstance(data, dict):
        raise SmsConfigError("短信配置根节点必须是对象")
    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise SmsConfigError("总开关必须是布尔值")
    provider = data.get("provider", "at_modem")
    if provider not in ALLOWED_PROVIDERS:
        raise SmsConfigError(
            "短信 Provider 仅支持 at_modem、generic_http、wxpusher、aliyun 或 tencent"
        )

    at_modem = data.get("at_modem", {}) or {}
    generic_http = data.get("generic_http", {}) or {}
    wxpusher = data.get("wxpusher", {}) or {}
    aliyun = data.get("aliyun", {}) or {}
    tencent = data.get("tencent", {}) or {}
    if not isinstance(at_modem, dict):
        raise SmsConfigError("at_modem 配置必须是对象")
    if not isinstance(generic_http, dict):
        raise SmsConfigError("generic_http 配置必须是对象")
    if not isinstance(wxpusher, dict):
        raise SmsConfigError("wxpusher 配置必须是对象")
    if not isinstance(aliyun, dict):
        raise SmsConfigError("aliyun 配置必须是对象")
    if not isinstance(tencent, dict):
        raise SmsConfigError("tencent 配置必须是对象")

    raw_recipients = data.get("phone_numbers", data.get("recipients", []))
    if not isinstance(raw_recipients, (str, list, tuple)):
        raise SmsConfigError("接收手机号必须是数组或分隔字符串")
    has_recipient = bool(
        raw_recipients.strip()
        if isinstance(raw_recipients, str)
        else any(str(item).strip() for item in raw_recipients)
    )
    recipients = parse_recipients(raw_recipients) if has_recipient else ()

    port = _string(at_modem.get("port", data.get("port", "")), "短信 COM 口").strip()
    template = _string(
        at_modem.get("template", data.get("template", SmsServiceConfig.template)),
        "短信模板",
    )
    encoding = _string(
        at_modem.get("encoding", data.get("encoding", "auto")), "短信编码"
    ).lower().strip()
    api_url = _string(generic_http.get("api_url", data.get("api_url", "")), "API URL").strip()
    request_method = _string(
        generic_http.get("request_method", data.get("request_method", "POST")),
        "HTTP 方法",
    ).upper().strip()
    token = _string(generic_http.get("token", data.get("token", "")), "token").strip()
    access_key = _string(
        generic_http.get("access_key", data.get("access_key", "")), "access_key"
    ).strip()
    access_secret = _string(
        generic_http.get("access_secret", data.get("access_secret", "")),
        "access_secret",
    ).strip()
    sign_name = _string(
        generic_http.get("sign_name", data.get("sign_name", "")), "签名名称"
    ).strip()
    template_id = _string(
        generic_http.get("template_id", data.get("template_id", "")), "模板 ID"
    ).strip()
    verify_ssl = generic_http.get("verify_ssl", data.get("verify_ssl", True))
    if not isinstance(verify_ssl, bool):
        raise SmsConfigError("verify_ssl 必须是布尔值")
    field_mapping = generic_http.get("field_mapping", data.get("field_mapping", {})) or {}
    field_mapping = _validate_field_mapping(field_mapping)

    wx_app_token = _string(
        wxpusher.get("app_token", ""), "WxPusher appToken"
    ).strip()
    wx_uids = _parse_wxpusher_uids(wxpusher.get("uids", []))
    wx_topic_ids = _parse_wxpusher_topic_ids(wxpusher.get("topic_ids", []))
    wx_content_type = wxpusher.get("content_type", 1)
    wx_summary_template = _string(
        wxpusher.get("summary_template", DEFAULT_WXPUSHER_SUMMARY_TEMPLATE),
        "WxPusher 摘要模板",
    )
    wx_api_url = _string(
        wxpusher.get("api_url", DEFAULT_WXPUSHER_API_URL), "WxPusher API URL"
    ).strip() or DEFAULT_WXPUSHER_API_URL
    wx_timeout = wxpusher.get("timeout_seconds", 10.0)
    wx_verify_ssl = wxpusher.get("verify_ssl", True)
    if not isinstance(wx_verify_ssl, bool):
        raise SmsConfigError("wxpusher.verify_ssl 必须是布尔值")

    aliyun_access_key_id = _string(aliyun.get("access_key_id", ""), "阿里云 AccessKeyId").strip()
    aliyun_access_key_secret = _string(
        aliyun.get("access_key_secret", ""), "阿里云 AccessKeySecret"
    ).strip()
    aliyun_sign_name = _string(aliyun.get("sign_name", ""), "阿里云签名").strip()
    aliyun_template_code = _string(aliyun.get("template_code", ""), "阿里云模板 code").strip()
    aliyun_region = _string(
        aliyun.get("region", "cn-hangzhou"), "阿里云 region"
    ).strip() or "cn-hangzhou"

    tencent_secret_id = _string(tencent.get("secret_id", ""), "腾讯云 SecretId").strip()
    tencent_secret_key = _string(tencent.get("secret_key", ""), "腾讯云 SecretKey").strip()
    tencent_sdk_app_id = str(tencent.get("sdk_app_id", "") or "").strip()
    tencent_sign_name = _string(tencent.get("sign_name", ""), "腾讯云签名").strip()
    tencent_template_id = str(tencent.get("template_id", "") or "").strip()
    tencent_region = _string(
        tencent.get("region", "ap-guangzhou"), "腾讯云 region"
    ).strip() or "ap-guangzhou"

    raw_backoff = data.get("retry_backoff_seconds")
    retry_count = data.get("retry_count", data.get("retries", 3))
    default_retry_delay = (
        raw_backoff[0]
        if isinstance(raw_backoff, (list, tuple)) and raw_backoff
        else 2.0
    )
    retry_delay = data.get("retry_delay_seconds", default_retry_delay)
    if raw_backoff is None:
        raw_backoff = [retry_delay] * max(1, int(retry_count) if str(retry_count).isdigit() else 1)
    if not isinstance(raw_backoff, (list, tuple)):
        raise SmsConfigError("重试退避必须是秒数数组")

    try:
        config = SmsServiceConfig(
            enabled=enabled,
            provider=provider,
            port=port,
            baudrate=int(at_modem.get("baudrate", data.get("baudrate", 115200))),
            recipients=recipients,
            template=template,
            encoding=encoding,
            api_url=api_url,
            request_method=request_method,
            timeout_seconds=float(
                generic_http.get("timeout_seconds", data.get("timeout_seconds", 10.0))
            ),
            token=token,
            access_key=access_key,
            access_secret=access_secret,
            sign_name=sign_name,
            template_id=template_id,
            verify_ssl=verify_ssl,
            field_mapping=field_mapping,
            wxpusher_app_token=wx_app_token,
            wxpusher_uids=wx_uids,
            wxpusher_topic_ids=wx_topic_ids,
            wxpusher_content_type=int(wx_content_type),
            wxpusher_summary_template=wx_summary_template,
            wxpusher_api_url=wx_api_url,
            wxpusher_timeout_seconds=float(wx_timeout),
            wxpusher_verify_ssl=wx_verify_ssl,
            aliyun_access_key_id=aliyun_access_key_id,
            aliyun_access_key_secret=aliyun_access_key_secret,
            aliyun_sign_name=aliyun_sign_name,
            aliyun_template_code=aliyun_template_code,
            aliyun_region=aliyun_region,
            tencent_secret_id=tencent_secret_id,
            tencent_secret_key=tencent_secret_key,
            tencent_sdk_app_id=tencent_sdk_app_id,
            tencent_sign_name=tencent_sign_name,
            tencent_template_id=tencent_template_id,
            tencent_region=tencent_region,
            retries=int(retry_count),
            retry_delay_seconds=float(retry_delay),
            retry_backoff_seconds=tuple(float(value) for value in raw_backoff),
            ng_threshold=int(data.get("ng_threshold", 5)),
            cooldown_seconds=float(data.get("cooldown_seconds", 60.0)),
            queue_size=int(data.get("queue_size", 100)),
            offline_queue_max=int(data.get("offline_queue_max", 200)),
            offline_ttl_seconds=float(data.get("offline_ttl_seconds", 86_400)),
            summary_schedule_mode=str(
                data.get("summary_schedule_mode", "rolling_12h")
            ).strip(),
            shift_start_hour=int(data.get("shift_start_hour", 8)),
            shift_end_hour=int(data.get("shift_end_hour", 20)),
            send_night_window=_bool(
                data.get("send_night_window", False), "send_night_window"
            ),
        )
    except (TypeError, ValueError) as exc:
        raise SmsConfigError("短信配置包含非法数值") from exc

    _validate_config(config)
    return config


def _validate_config(config: SmsServiceConfig) -> None:
    validate_alarm_template(config.template)
    choose_encoding("test", config.encoding)
    if not 300 <= config.baudrate <= 4_000_000:
        raise SmsConfigError("短信波特率超出允许范围")
    if not 0 <= config.retries <= 5:
        raise SmsConfigError("重试次数超出允许范围")
    if not 0 <= config.retry_delay_seconds <= 300:
        raise SmsConfigError("重试间隔超出允许范围")
    if not 1 <= len(config.retry_backoff_seconds) <= 10 or any(
        value < 0 or value > 300 for value in config.retry_backoff_seconds
    ):
        raise SmsConfigError("重试退避数组必须包含 1~10 个 0~300 秒数值")
    if not 1 <= config.ng_threshold <= 100:
        raise SmsConfigError("每工位 NG 累计阈值必须在 1~100 之间")
    if not 0 <= config.cooldown_seconds <= 86_400:
        raise SmsConfigError("冷却时间超出允许范围")
    if not 1 <= config.queue_size <= 1_000:
        raise SmsConfigError("内存队列容量超出允许范围")
    if not 1 <= config.offline_queue_max <= 10_000:
        raise SmsConfigError("离线队列容量超出允许范围")
    if not 60 <= config.offline_ttl_seconds <= 30 * 86_400:
        raise SmsConfigError("离线短信 TTL 必须在 60 秒到 30 天之间")
    if len(config.port) > 128:
        raise SmsConfigError("短信 COM 口名称过长")
    if len(config.recipients) > 20:
        raise SmsConfigError("接收手机号最多 20 个")
    if len(config.template) > 2_000:
        raise SmsConfigError("短信模板过长")
    if config.request_method not in ALLOWED_HTTP_METHODS:
        raise SmsConfigError("generic_http 仅支持 POST 或 PUT JSON 请求")
    if not 0.5 <= config.timeout_seconds <= 120:
        raise SmsConfigError("HTTP 超时必须在 0.5~120 秒之间")
    for name, value, limit in (
        ("token", config.token, 8_192),
        ("access_key", config.access_key, 2_048),
        ("access_secret", config.access_secret, 8_192),
        ("sign_name", config.sign_name, 200),
        ("template_id", config.template_id, 500),
    ):
        if len(value) > limit:
            raise SmsConfigError(f"{name} 过长")
    if config.api_url:
        _validate_http_url(config.api_url)
    if bool(config.access_key) != bool(config.access_secret):
        raise SmsConfigError("access_key 与 access_secret 必须成对配置")
    if config.wxpusher_content_type not in ALLOWED_WXPUSHER_CONTENT_TYPES:
        raise SmsConfigError("WxPusher content_type 仅支持 1/2/3")
    if len(config.wxpusher_app_token) > 512:
        raise SmsConfigError("WxPusher appToken 过长")
    if len(config.wxpusher_uids) > 50:
        raise SmsConfigError("WxPusher UID 最多 50 个")
    if len(config.wxpusher_topic_ids) > 20:
        raise SmsConfigError("WxPusher TopicId 最多 20 个")
    if len(config.wxpusher_summary_template) > 500:
        raise SmsConfigError("WxPusher 摘要模板过长")
    if not 0.5 <= config.wxpusher_timeout_seconds <= 120:
        raise SmsConfigError("WxPusher HTTP 超时必须在 0.5~120 秒之间")
    if config.wxpusher_api_url:
        _validate_http_url(config.wxpusher_api_url)
    validate_alarm_template(config.wxpusher_summary_template)
    for name, value, limit in (
        ("阿里云 AccessKeyId", config.aliyun_access_key_id, 256),
        ("阿里云 AccessKeySecret", config.aliyun_access_key_secret, 512),
        ("阿里云签名", config.aliyun_sign_name, 200),
        ("阿里云模板 code", config.aliyun_template_code, 100),
        ("阿里云 region", config.aliyun_region, 64),
        ("腾讯云 SecretId", config.tencent_secret_id, 256),
        ("腾讯云 SecretKey", config.tencent_secret_key, 512),
        ("腾讯云 SdkAppId", config.tencent_sdk_app_id, 64),
        ("腾讯云签名", config.tencent_sign_name, 200),
        ("腾讯云模板 ID", config.tencent_template_id, 100),
        ("腾讯云 region", config.tencent_region, 64),
    ):
        if len(value) > limit:
            raise SmsConfigError(f"{name} 过长")
    if config.enabled and config.provider != "wxpusher" and not config.recipients:
        raise SmsConfigError("开启 NG 短信推送前必须配置接收手机号")
    if config.enabled and config.provider == "at_modem" and not config.port:
        raise SmsConfigError("开启 USB/AT 短信前必须配置短信 COM 口")
    if config.enabled and config.provider == "generic_http":
        if not config.api_url:
            raise SmsConfigError("开启云短信前必须配置 API URL")
        if not (config.token or (config.access_key and config.access_secret)):
            raise SmsConfigError("开启云短信前必须配置 token 或 access_key/access_secret")
    if config.enabled and config.provider == "wxpusher":
        if not config.wxpusher_app_token:
            raise SmsConfigError("开启微信推送前必须配置 WxPusher appToken")
        if not config.wxpusher_uids and not config.wxpusher_topic_ids:
            raise SmsConfigError("开启微信推送前必须配置 UID 或 TopicId")
    if config.enabled and config.provider == "aliyun":
        if not (config.aliyun_access_key_id and config.aliyun_access_key_secret):
            raise SmsConfigError("开启阿里云短信前必须配置 AccessKey")
        if not config.aliyun_sign_name:
            raise SmsConfigError("开启阿里云短信前必须配置审核过的签名")
        if not config.aliyun_template_code:
            raise SmsConfigError("开启阿里云短信前必须配置审核过的模板 code")
    if config.enabled and config.provider == "tencent":
        if not (config.tencent_secret_id and config.tencent_secret_key):
            raise SmsConfigError("开启腾讯云短信前必须配置 SecretId/SecretKey")
        if not config.tencent_sdk_app_id:
            raise SmsConfigError("开启腾讯云短信前必须配置短信应用 SdkAppId")
        if not config.tencent_sign_name:
            raise SmsConfigError("开启腾讯云短信前必须配置审核过的签名")
        if not config.tencent_template_id:
            raise SmsConfigError("开启腾讯云短信前必须配置审核过的模板 ID")
    if config.summary_schedule_mode not in ALLOWED_SUMMARY_SCHEDULE_MODES:
        raise SmsConfigError(
            "汇总调度模式仅支持 rolling_12h 或 daily_shift"
        )
    if not 0 <= config.shift_start_hour <= 23:
        raise SmsConfigError("班次开始小时必须在 0~23")
    if not 0 <= config.shift_end_hour <= 23:
        raise SmsConfigError("班次结束小时必须在 0~23")
    if config.shift_start_hour == config.shift_end_hour:
        raise SmsConfigError("班次开始与结束小时不能相同")
    if (
        config.summary_schedule_mode == "daily_shift"
        and not config.send_night_window
        and config.shift_start_hour >= config.shift_end_hour
    ):
        raise SmsConfigError(
            "仅白天班次时，开始小时必须早于结束小时（如 8 点到 20 点）"
        )


def _validate_http_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise SmsConfigError("云短信 API URL 必须是有效的 HTTP/HTTPS 地址")
    if parsed.username or parsed.password:
        raise SmsConfigError("云短信 API URL 禁止内嵌用户名或密码")


def _validate_field_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise SmsConfigError("field_mapping 必须是对象")
    if len(value) > 50:
        raise SmsConfigError("field_mapping 最多 50 项")
    normalized: dict[str, str] = {}
    targets: set[str] = set()
    for raw_source, raw_target in value.items():
        if not isinstance(raw_source, str) or raw_source not in ALLOWED_MAPPING_SOURCES:
            raise SmsConfigError(f"不支持的字段映射源：{raw_source}")
        if not isinstance(raw_target, str):
            raise SmsConfigError("字段映射目标必须是点路径字符串")
        target = raw_target.strip()
        parts = target.split(".")
        if not target or any(not part.isidentifier() for part in parts):
            raise SmsConfigError(f"字段映射目标不是安全点路径：{raw_target}")
        if target in targets:
            raise SmsConfigError(f"字段映射目标重复：{target}")
        normalized[raw_source] = target
        targets.add(target)
    return normalized


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise SmsConfigError(f"{label} 必须是字符串")
    return value


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise SmsConfigError(f"{label} 必须是布尔值")
    return value


def _parse_wxpusher_uids(value: object) -> tuple[str, ...]:
    if value in (None, "", []):
        return ()
    if isinstance(value, str):
        raw_items = [part.strip() for part in value.replace("；", ";").split(";")]
        raw_items = [part for item in raw_items for part in item.split(",")]
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raise SmsConfigError("WxPusher UID 必须是数组或分隔字符串")
    uids: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > 128:
            raise SmsConfigError("WxPusher UID 过长")
        if text not in seen:
            uids.append(text)
            seen.add(text)
    return tuple(uids)


def _parse_wxpusher_topic_ids(value: object) -> tuple[int, ...]:
    if value in (None, "", []):
        return ()
    if isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raise SmsConfigError("WxPusher TopicId 必须是数组")
    topic_ids: list[int] = []
    seen: set[int] = set()
    for item in raw_items:
        try:
            topic_id = int(item)
        except (TypeError, ValueError) as exc:
            raise SmsConfigError("WxPusher TopicId 必须是整数") from exc
        if topic_id <= 0:
            raise SmsConfigError("WxPusher TopicId 必须为正整数")
        if topic_id not in seen:
            topic_ids.append(topic_id)
            seen.add(topic_id)
    return tuple(topic_ids)


class SmsConfigStore:
    """以原子替换方式读写独立短信配置文件。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else Path(DATA_DIR) / SMS_CONFIG_FILENAME
        self._lock = threading.RLock()

    def load(self) -> SmsServiceConfig:
        """缺失或损坏时回退默认关闭；不创建配置或离线队列文件。"""

        with self._lock:
            if not self.path.exists():
                return SmsServiceConfig()
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                return config_from_dict(data)
            except Exception:
                logger.error("短信配置文件无效，已回退为默认关闭")
                return SmsServiceConfig()

    def save(self, config: SmsServiceConfig) -> None:
        """严格校验后原子写入；失败时保留原文件。"""

        validated = config_from_dict(config_to_dict(config))
        payload = json.dumps(config_to_dict(validated), ensure_ascii=False, indent=2)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="\n",
                    dir=self.path.parent,
                    prefix=".sms_config_",
                    suffix=".tmp",
                    delete=False,
                ) as temp_file:
                    temp_path = temp_file.name
                    temp_file.write(payload)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_path, self.path)
            except OSError as exc:
                if temp_path:
                    try:
                        Path(temp_path).unlink(missing_ok=True)
                    except OSError:
                        pass
                raise SmsConfigError("短信配置保存失败") from exc
