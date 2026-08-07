"""腾讯云短信 (SendSms 2021-01-11) Provider。

- POST JSON + TC3-HMAC-SHA256 签名, 用 requests 直调不引 SDK
- 云短信是"审核签名 + 审核模板 + 位置变量":
  * ``context["template_params"]`` 的 dict 插入顺序 = 云模板 {1}{2}... 对位
  * 缺省时回退 NG 汇总三变量 (time_range, ok_count, ng_count) 的位置序
  * ``context["template_code"]`` 可覆盖配置默认模板 ID
- 成功判定: 所有 SendStatusSet[].Code == "Ok"; 支持逐号成败拆分
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

import requests

from backend.services.sms_providers.aliyun_provider import resolve_template_params
from backend.services.sms_providers.base_provider import (
    RecipientResult,
    SmsBatchResult,
    SmsProvider,
)


TENCENT_HOST = "sms.tencentcloudapi.com"
_SERVICE = "sms"
_VERSION = "2021-01-11"


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def build_tc3_headers(
    payload: str, secret_id: str, secret_key: str, region: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """TC3-HMAC-SHA256 签名 (官方签名方法 v3)。"""

    now = now or datetime.now(timezone.utc)
    timestamp = str(int(now.timestamp()))
    date = now.strftime("%Y-%m-%d")

    content_type = "application/json; charset=utf-8"
    canonical_request = "\n".join([
        "POST", "/", "",
        f"content-type:{content_type}\nhost:{TENCENT_HOST}\n",
        "content-type;host",
        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    ])
    credential_scope = f"{date}/{_SERVICE}/tc3_request"
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256", timestamp, credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac_sha256(secret_date, _SERVICE)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(
        secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    return {
        "Content-Type": content_type,
        "Host": TENCENT_HOST,
        "Authorization": (
            f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
            f"SignedHeaders=content-type;host, Signature={signature}"
        ),
        "X-TC-Action": "SendSms",
        "X-TC-Version": _VERSION,
        "X-TC-Timestamp": timestamp,
        "X-TC-Region": region,
    }


class TencentProvider(SmsProvider):
    """通过腾讯云 SendSms 发送模板短信。"""

    name = "tencent"

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
        template_id = (
            str(context.get("template_code", "") or "").strip()
            or self.config.tencent_template_id
        )
        if not template_id:
            return self._failure(
                recipients, attempts=0, detail="未配置腾讯云模板 ID",
                status_code=None, error_code="TENCENT_NO_TEMPLATE", retryable=False,
            )

        # 国内手机号腾讯云要求 +86 前缀
        phones = [
            p if str(p).startswith("+") else f"+86{str(p).strip()}"
            for p in recipients
        ]
        payload = json.dumps({
            "PhoneNumberSet": phones,
            "SmsSdkAppId": str(self.config.tencent_sdk_app_id),
            "SignName": self.config.tencent_sign_name,
            "TemplateId": template_id,
            "TemplateParamSet": list(resolve_template_params(context).values()),
        }, ensure_ascii=False)

        max_attempts = max(1, int(self.config.retries) + 1)
        last_status: int | None = None
        last_error_code = "TENCENT_UNKNOWN"
        last_detail = "腾讯云短信请求失败"
        retryable = False

        for attempt in range(1, max_attempts + 1):
            if self._stop_event.is_set():
                return self._failure(
                    recipients, attempts=max(0, attempt - 1),
                    detail="短信服务正在关闭", status_code=None,
                    error_code="SMS_STOPPED", retryable=False,
                )
            try:
                headers = build_tc3_headers(
                    payload,
                    self.config.tencent_secret_id,
                    self.config.tencent_secret_key,
                    self.config.tencent_region or "ap-guangzhou",
                )
                response = self._request(
                    method="POST",
                    url=f"https://{TENCENT_HOST}/",
                    data=payload.encode("utf-8"),
                    headers=headers,
                    timeout=float(self.config.timeout_seconds),
                )
                last_status = int(getattr(response, "status_code"))
                body = self._safe_json(response)
                inner = body.get("Response") or {}
                error = inner.get("Error")
                if error:
                    code = str(error.get("Code", "") or "")
                    retryable = last_status >= 500 or code in {
                        "InternalError", "RequestLimitExceeded",
                        "LimitExceeded.PhoneNumberDailyLimit",
                    }
                    last_error_code = f"TENCENT_{code or last_status}"
                    last_detail = self._redact(
                        f"腾讯云返回 {code}: {error.get('Message', '')}"
                    )[:200]
                else:
                    statuses = inner.get("SendStatusSet") or []
                    if last_status == 200 and statuses:
                        return self._per_phone_result(
                            recipients, phones, statuses, attempt, last_status
                        )
                    retryable = last_status >= 500
                    last_error_code = f"HTTP_{last_status}"
                    last_detail = f"腾讯云响应异常: HTTP {last_status}"
            except requests.Timeout:
                retryable, last_status = True, None
                last_error_code, last_detail = "HTTP_TIMEOUT", "腾讯云短信请求超时"
            except requests.RequestException:
                retryable, last_status = True, None
                last_error_code, last_detail = "HTTP_OFFLINE", "无法连接腾讯云短信接口"
            except Exception as exc:
                retryable, last_status = False, None
                last_error_code = "TENCENT_CLIENT_ERROR"
                last_detail = f"腾讯云短信客户端异常：{type(exc).__name__}"

            self._logger(
                f"腾讯云短信第 {attempt}/{max_attempts} 次请求失败：{last_error_code}"
            )
            if not retryable or attempt >= max_attempts:
                return self._failure(
                    recipients, attempts=attempt, detail=last_detail,
                    status_code=last_status, error_code=last_error_code,
                    retryable=retryable,
                )
            self._stop_event.wait(self._retry_delay(attempt - 1))

        raise AssertionError("tencent retry loop must return")

    @staticmethod
    def _per_phone_result(
        recipients: Sequence[str],
        prefixed_phones: Sequence[str],
        statuses: Sequence[Mapping[str, object]],
        attempt: int,
        status_code: int,
    ) -> SmsBatchResult:
        """SendStatusSet 逐号拆分成败 (顺序与请求 PhoneNumberSet 一致)。"""

        by_phone: dict[str, Mapping[str, object]] = {}
        for status in statuses:
            by_phone[str(status.get("PhoneNumber", "") or "")] = status
        results = []
        for original, prefixed in zip(recipients, prefixed_phones):
            status = by_phone.get(prefixed) or (
                statuses[len(results)] if len(results) < len(statuses) else {}
            )
            code = str(status.get("Code", "") or "")
            ok = code == "Ok"
            results.append(RecipientResult(
                phone=str(original),
                success=ok,
                attempts=attempt,
                detail=(
                    "腾讯云已接受请求；最终送达由运营商确认" if ok
                    else f"{code}: {status.get('Message', '')}"[:200]
                ),
                message_reference=str(status.get("SerialNo", "") or ""),
                status_code=status_code,
                error_code="" if ok else f"TENCENT_{code or 'UNKNOWN'}",
            ))
        return SmsBatchResult(tuple(results))

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
            self.config.tencent_secret_id,
            self.config.tencent_secret_key,
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
