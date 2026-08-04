"""
腾讯云短信 (SendSms 2021-01-11) 适配器

- POST JSON + TC3-HMAC-SHA256 签名, 用 requests 直调不引 SDK
- config 字段:
    access_key_id / access_key_secret : 腾讯云 SecretId/SecretKey
    sign_name                         : 审核过的短信签名
    template_code                     : 默认模板 ID (纯数字字符串)
    sms_sdk_app_id                    : 短信应用 SdkAppId (腾讯云特有, 必填)
    region                            : 默认 "ap-guangzhou"
- 模板变量: 位置参数 (TemplateParamSet 有序列表)。取 template_params dict 的
  插入顺序 —— 上游按规则映射顺序构造 dict, 与云模板 {1}{2}... 对位
- 成功判定: 所有 SendStatusSet[].Code == "Ok"
"""
import datetime
import hashlib
import hmac
import json

import requests

from .base import BaseSmsAdapter

_HOST = "sms.tencentcloudapi.com"
_SERVICE = "sms"
_VERSION = "2021-01-11"


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


class TencentSmsAdapter(BaseSmsAdapter):
    provider = "tencent"

    def send(self, phone_numbers, template_params, config, template_code=None) -> dict:
        try:
            tpl = self._resolve_template_code(config, template_code)
            if not tpl:
                return {"success": False, "error": "缺少模板 ID (template_code)", "response": None}
            sign_name = (config.get("sign_name") or "").strip()
            if not sign_name:
                return {"success": False, "error": "缺少短信签名 (sign_name)", "response": None}
            secret_id = (config.get("access_key_id") or "").strip()
            secret_key = (config.get("access_key_secret") or "").strip()
            app_id = str(config.get("sms_sdk_app_id") or "").strip()
            if not secret_id or not secret_key:
                return {"success": False, "error": "缺少 SecretId/SecretKey", "response": None}
            if not app_id:
                return {"success": False, "error": "缺少短信应用 SdkAppId (sms_sdk_app_id)", "response": None}

            # 国内手机号腾讯云要求 +86 前缀
            phones = [p if p.startswith("+") else f"+86{p}" for p in (phone_numbers or [])]
            payload = json.dumps({
                "PhoneNumberSet": phones,
                "SmsSdkAppId": app_id,
                "SignName": sign_name,
                "TemplateId": tpl,
                "TemplateParamSet": list(self._stringify_params(template_params).values()),
            }, ensure_ascii=False)

            headers = self._build_headers(payload, secret_id, secret_key,
                                          config.get("region") or "ap-guangzhou")
            resp = requests.post(f"https://{_HOST}/", data=payload.encode("utf-8"),
                                 headers=headers,
                                 timeout=int(config.get("timeout") or 10))
            try:
                body = resp.json()
            except Exception:
                body = {"raw": resp.text[:500]}

            r = body.get("Response") or {}
            if r.get("Error"):
                err = r["Error"]
                return {"success": False,
                        "error": f"{err.get('Code')}: {err.get('Message')}",
                        "response": {"status_code": resp.status_code, "body": body}}
            statuses = r.get("SendStatusSet") or []
            ok = resp.status_code == 200 and statuses and all(
                s.get("Code") == "Ok" for s in statuses)
            err_msg = None
            if not ok:
                bad = [s for s in statuses if s.get("Code") != "Ok"]
                err_msg = "; ".join(f"{s.get('PhoneNumber')}: {s.get('Code')} {s.get('Message')}"
                                    for s in bad) or f"HTTP {resp.status_code}"
            return {"success": ok, "error": err_msg,
                    "response": {"status_code": resp.status_code, "body": body}}
        except Exception as e:
            return {"success": False, "error": f"腾讯云短信调用异常: {e}", "response": None}

    @staticmethod
    def _build_headers(payload: str, secret_id: str, secret_key: str, region: str) -> dict:
        """TC3-HMAC-SHA256 签名 (官方签名方法 v3)"""
        now = datetime.datetime.utcnow()
        # utcnow() 是 naive datetime, .timestamp() 会按本地时区偏移 —— 用显式 UTC 纪元差
        epoch = datetime.datetime(1970, 1, 1)
        timestamp = str(int((now - epoch).total_seconds()))
        date = now.strftime("%Y-%m-%d")

        ct = "application/json; charset=utf-8"
        canonical_request = "\n".join([
            "POST", "/", "",
            f"content-type:{ct}\nhost:{_HOST}\n",
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
        signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"),
                             hashlib.sha256).hexdigest()

        return {
            "Content-Type": ct,
            "Host": _HOST,
            "Authorization": (
                f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
                f"SignedHeaders=content-type;host, Signature={signature}"
            ),
            "X-TC-Action": "SendSms",
            "X-TC-Version": _VERSION,
            "X-TC-Timestamp": timestamp,
            "X-TC-Region": region,
        }
