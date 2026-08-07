"""阿里云短信 (Dysmsapi SendSms) Provider。

- RPC 风格 API, HMAC-SHA1 签名 (Version 2017-05-25), 用 requests 直调不引 SDK
- 云短信是"审核签名 + 审核模板 + 命名变量", 不能发自由文本:
  * ``context["template_params"]`` 提供命名变量 dict (日报路径显式传入)
  * 缺省时回退 NG 汇总三变量 time_range / ok_count / ng_count (报警汇总路径)
  * ``context["template_code"]`` 可覆盖配置默认模板 (日报规则级覆盖)
- 成功判定: 响应 JSON Code == "OK"
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from backend.services.sms_providers.base_provider import (
    RecipientResult,
    SmsBatchResult,
    SmsProvider,
)


ALIYUN_ENDPOINT = "https://dysmsapi.aliyuncs.com/"


def _percent_encode(value: object) -> str:
    """阿里云 RPC 签名要求的 percent-encode 变体。"""

    return (
        quote(str(value), safe="~")
        .replace("+", "%20")
        .replace("*", "%2A")
        .replace("%7E", "~")
    )


def sign_aliyun_rpc(params: Mapping[str, str], access_key_secret: str) -> str:
    """阿里云 RPC HMAC-SHA1 签名 (GET 方式)。"""

    sorted_qs = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted(params.items())
    )
    string_to_sign = "GET&%2F&" + _percent_encode(sorted_qs)
    digest = hmac.new(
        (access_key_secret + "&").encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def resolve_template_params(context: Mapping[str, object]) -> dict[str, str]:
    """模板变量：显式 template_params 优先，否则回退 NG 汇总三变量。"""

    explicit = context.get("template_params")
    if isinstance(explicit, Mapping) and explicit:
        return {str(k): str(v) for k, v in explicit.items()}
    return {
        "time_range": str(context.get("time_range", "") or ""),
        "ok_count": str(int(context.get("ok_count", 0) or 0)),
        "ng_count": str(int(context.get("ng_count", 0) or 0)),
    }


class AliyunProvider(SmsProvider):
    """通过阿里云 Dysmsapi 发送模板短信。"""

    name = "aliyun"

    def __init__(
        self,
        config: Any,
        *,
        request_func: Callable[..., object] | None = None,
        logger: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self._request = request_func or requests.request
        self._logger = logger or (lambda _message: None)
        self._stop_event = stop_event or threading.Event()

    def send(
        self,
        recipients: Sequence[str],
        message: str,
        *,
        message_id: str,
        event_name: str,
        raw_message: str,
        context: Mapping[str, object],
    ) -> SmsBatchResult:
        del message, event_name, raw_message  # 云模板通道不发自由文本
        template_code = (
            str(context.get("template_code", "") or "").strip()
            or self.config.aliyun_template_code
        )
        if not template_code:
            return self._failure(
                recipients, attempts=0, detail="未配置阿里云模板 code",
                status_code=None, error_code="ALIYUN_NO_TEMPLATE", retryable=False,
            )

        template_params = resolve_template_params(context)
        max_attempts = max(1, int(self.config.retries) + 1)
        last_status: int | None = None
        last_error_code = "ALIYUN_UNKNOWN"
        last_detail = "阿里云短信请求失败"
        retryable = False

        for attempt in range(1, max_attempts + 1):
            if self._stop_event.is_set():
                return self._failure(
                    recipients, attempts=max(0, attempt - 1),
                    detail="短信服务正在关闭", status_code=None,
                    error_code="SMS_STOPPED", retryable=False,
                )
            params = {
                "Action": "SendSms",
                "Version": "2017-05-25",
                "Format": "JSON",
                "AccessKeyId": self.config.aliyun_access_key_id,
                "SignatureMethod": "HMAC-SHA1",
                "SignatureVersion": "1.0",
                "SignatureNonce": str(uuid.uuid4()),
                "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "RegionId": self.config.aliyun_region or "cn-hangzhou",
                "PhoneNumbers": ",".join(str(p).strip() for p in recipients),
                "SignName": self.config.aliyun_sign_name,
                "TemplateCode": template_code,
                "TemplateParam": json.dumps(template_params, ensure_ascii=False),
            }
            params["Signature"] = sign_aliyun_rpc(
                params, self.config.aliyun_access_key_secret
            )

            try:
                response = self._request(
                    method="GET",
                    url=ALIYUN_ENDPOINT,
                    params=params,
                    timeout=float(self.config.timeout_seconds),
                )
                last_status = int(getattr(response, "status_code"))
                body = self._safe_json(response)
                code = str(body.get("Code", "") or "")
                if last_status == 200 and code == "OK":
                    biz_id = str(body.get("BizId", "") or "") or message_id
                    return SmsBatchResult(
                        tuple(
                            RecipientResult(
                                phone=str(phone),
                                success=True,
                                attempts=attempt,
                                detail="阿里云已接受请求；最终送达由运营商确认",
                                message_reference=biz_id,
                                status_code=last_status,
                            )
                            for phone in recipients
                        ),
                        provider_metadata={"biz_id": biz_id, "code": code},
                    )
                # 限流/服务端错误可重试; 签名/参数错误重试无意义
                retryable = last_status >= 500 or code in {
                    "isv.BUSINESS_LIMIT_CONTROL",
                    "SYSTEM_ERROR",
                    "Throttling",
                }
                last_error_code = f"ALIYUN_{code or last_status}"
                last_detail = self._redact(
                    f"阿里云返回 {code or last_status}: {body.get('Message', '')}"
                )[:200]
            except requests.Timeout:
                retryable, last_status = True, None
                last_error_code, last_detail = "HTTP_TIMEOUT", "阿里云短信请求超时"
            except requests.RequestException:
                retryable, last_status = True, None
                last_error_code, last_detail = "HTTP_OFFLINE", "无法连接阿里云短信接口"
            except Exception as exc:
                retryable, last_status = False, None
                last_error_code = "ALIYUN_CLIENT_ERROR"
                last_detail = f"阿里云短信客户端异常：{type(exc).__name__}"

            self._logger(
                f"阿里云短信第 {attempt}/{max_attempts} 次请求失败：{last_error_code}"
            )
            if not retryable or attempt >= max_attempts:
                return self._failure(
                    recipients, attempts=attempt, detail=last_detail,
                    status_code=last_status, error_code=last_error_code,
                    retryable=retryable,
                )
            self._stop_event.wait(self._retry_delay(attempt - 1))

        raise AssertionError("aliyun retry loop must return")

    def _retry_delay(self, index: int) -> float:
        backoff = tuple(self.config.retry_backoff_seconds or ())
        if not backoff:
            return 0.0
        return max(0.0, float(backoff[min(index, len(backoff) - 1)]))

    @staticmethod
    def _safe_json(response: object) -> dict:
        try:
            payload = response.json()
        except Exception:
            return {"raw": str(getattr(response, "text", ""))[:200]}
        return payload if isinstance(payload, dict) else {}

    def _redact(self, value: str) -> str:
        redacted = value
        for secret in (
            self.config.aliyun_access_key_id,
            self.config.aliyun_access_key_secret,
        ):
            if secret:
                redacted = redacted.replace(str(secret), "***")
        return redacted

    @staticmethod
    def _failure(
        recipients: Sequence[str],
        *,
        attempts: int,
        detail: str,
        status_code: int | None,
        error_code: str,
        retryable: bool,
    ) -> SmsBatchResult:
        return SmsBatchResult(
            tuple(
                RecipientResult(
                    phone=str(phone),
                    success=False,
                    attempts=attempts,
                    detail=detail,
                    status_code=status_code,
                    error_code=error_code,
                    retryable=retryable,
                )
                for phone in recipients
            )
        )
