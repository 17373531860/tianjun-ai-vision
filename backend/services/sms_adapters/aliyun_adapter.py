"""
阿里云短信 (Dysmsapi SendSms) 适配器

- RPC 风格 API, HMAC-SHA1 签名 (Version 2017-05-25), 用 requests 直调不引 SDK
- config 字段:
    access_key_id / access_key_secret : 阿里云 AK/SK
    sign_name                         : 审核过的短信签名 (如 "天军视觉")
    template_code                     : 默认模板 code (如 "SMS_123456789")
    region                            : 默认 "cn-hangzhou"
- 模板变量: 命名 JSON (TemplateParam), 直接用 template_params dict
- 成功判定: 响应 JSON Code == "OK"
"""
import base64
import datetime
import hashlib
import hmac
import json
import uuid
from urllib.parse import quote

import requests

from .base import BaseSmsAdapter

_ENDPOINT = "https://dysmsapi.aliyuncs.com/"


def _percent_encode(s: str) -> str:
    # 阿里云 RPC 签名要求的 percent-encode 变体
    return quote(str(s), safe="~").replace("+", "%20").replace("*", "%2A").replace("%7E", "~")


class AliyunSmsAdapter(BaseSmsAdapter):
    provider = "aliyun"

    def send(self, phone_numbers, template_params, config, template_code=None) -> dict:
        try:
            tpl = self._resolve_template_code(config, template_code)
            if not tpl:
                return {"success": False, "error": "缺少模板 code (template_code)", "response": None}
            sign_name = (config.get("sign_name") or "").strip()
            if not sign_name:
                return {"success": False, "error": "缺少短信签名 (sign_name)", "response": None}
            ak = (config.get("access_key_id") or "").strip()
            sk = (config.get("access_key_secret") or "").strip()
            if not ak or not sk:
                return {"success": False, "error": "缺少 AccessKey (access_key_id/secret)", "response": None}

            params = {
                "Action": "SendSms",
                "Version": "2017-05-25",
                "Format": "JSON",
                "AccessKeyId": ak,
                "SignatureMethod": "HMAC-SHA1",
                "SignatureVersion": "1.0",
                "SignatureNonce": str(uuid.uuid4()),
                "Timestamp": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "RegionId": config.get("region") or "cn-hangzhou",
                "PhoneNumbers": ",".join(phone_numbers or []),
                "SignName": sign_name,
                "TemplateCode": tpl,
                "TemplateParam": json.dumps(
                    self._stringify_params(template_params), ensure_ascii=False),
            }
            params["Signature"] = self._sign(params, sk)

            resp = requests.get(_ENDPOINT, params=params,
                                timeout=int(config.get("timeout") or 10))
            try:
                body = resp.json()
            except Exception:
                body = {"raw": resp.text[:500]}

            ok = resp.status_code == 200 and body.get("Code") == "OK"
            return {
                "success": ok,
                "error": None if ok else f"{body.get('Code')}: {body.get('Message')}",
                "response": {"status_code": resp.status_code, "body": body},
            }
        except Exception as e:
            return {"success": False, "error": f"阿里云短信调用异常: {e}", "response": None}

    @staticmethod
    def _sign(params: dict, access_key_secret: str) -> str:
        """阿里云 RPC HMAC-SHA1 签名 (GET 方式)"""
        sorted_qs = "&".join(
            f"{_percent_encode(k)}={_percent_encode(v)}"
            for k, v in sorted(params.items())
        )
        string_to_sign = "GET&%2F&" + _percent_encode(sorted_qs)
        digest = hmac.new(
            (access_key_secret + "&").encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")
