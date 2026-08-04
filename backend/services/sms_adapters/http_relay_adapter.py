"""
自建 HTTP 中转短信适配器 (v3.46+)

场景: 云短信"签名+模板"审核未过的过渡期, 或客户有自有短信网关。
把日报变量渲染成**完整短信正文**, POST 到自建中转服务 (如 tools/sms_relay/),
由中转侧的真实通道 (个人 SIM 卡 / 客户网关) 完成发送。

与云适配器的关键差异: 无平台模板审核, content_template 在本端渲染自由文本;
${变量名} 占位符取自规则的"变量映射", 与云模板变量同一套命名。

config 字段:
  relay_url        中转服务接收地址 (https://your.domain/send)
  relay_token      Bearer 鉴权 token (与中转服务约定一致)
  content_template 短信正文模板, 例:
                   "【天军视觉】${date}日报: 总数${total} 良率${rate}%"
                   留空则退化为 "k:v k:v" 拼接
"""
import re

import requests

from .base import BaseSmsAdapter


class HttpRelaySmsAdapter(BaseSmsAdapter):
    provider = "http_relay"

    TIMEOUT_S = 15

    def send(self, phone_numbers, template_params, config, template_code=None) -> dict:
        url = (config.get("relay_url") or "").strip()
        token = (config.get("relay_token") or "").strip()
        if not url:
            return {"success": False, "error": "http_relay 配置缺 relay_url", "response": None}

        params = self._stringify_params(template_params)
        content = self._render_content(config.get("content_template") or "", params)

        payload = {
            "phones": [str(p).strip() for p in (phone_numbers or []) if str(p).strip()],
            "content": content,
            # 原始变量一并携带, 中转侧要自行排版时可用
            "params": params,
        }
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.TIMEOUT_S)
        except Exception as e:
            return {"success": False, "error": f"中转服务请求失败: {e}", "response": None}

        try:
            body = resp.json()
        except Exception:
            body = {"raw": (resp.text or "")[:200]}

        if resp.status_code == 200 and body.get("success") is not False:
            return {"success": True, "error": None,
                    "response": {"status_code": resp.status_code, "body": body,
                                 "content": content}}
        return {"success": False,
                "error": f"中转服务返回异常: HTTP {resp.status_code} {str(body)[:150]}",
                "response": {"status_code": resp.status_code, "body": body}}

    @staticmethod
    def _render_content(template: str, params: dict) -> str:
        """把 ${变量名} 替换为变量值 (支持中文变量名); 无模板则 k:v 拼接。"""
        if not template.strip():
            return " ".join(f"{k}:{v}" for k, v in params.items())
        return re.sub(r"\$\{([^}]+)\}",
                      lambda m: params.get(m.group(1).strip(), ""), template)
