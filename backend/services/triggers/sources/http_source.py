"""http — 外部系统调 URL 触发 (被动源, 无线程)。

params:
    key          str    触发键 → POST /api/v1/triggers/fire/{key}
    secret       str    共享密钥 (可选; 配了则请求须带 X-Trigger-Secret 头或 ?secret=)
    ip_allow     [str]  IP 白名单前缀 (可选; 空 = 不限, 对齐入站对接内网免鉴权先例)
    extract      {var: json_path}  请求 body 变量提取 (可选, 点路径), 进动作模板上下文

信号语义: 脉冲型。API 层校验通过后调 fire(payload)。
"""
from typing import Optional

from backend.services.triggers.sources.base import BaseTriggerSource
from backend.services.triggers.actions import ctx_get


class HttpSource(BaseTriggerSource):
    type_name = "http"
    kind = "pulse"

    def __init__(self, params, emit_level, emit_pulse):
        super().__init__(params, emit_level, emit_pulse)
        err = self.validate_params(self.params)
        if err:
            raise ValueError(err)
        self.key = str(self.params["key"]).strip()
        self.secret = (self.params.get("secret") or "").strip()
        self.ip_allow = [s.strip() for s in (self.params.get("ip_allow") or []) if s.strip()]
        self.extract = self.params.get("extract") or {}
        self._recv_count = 0

    @classmethod
    def validate_params(cls, params: dict) -> Optional[str]:
        key = str((params or {}).get("key") or "").strip()
        if not key:
            return "http 触发源需要 key (触发 URL 的路径段)"
        if not key.replace("-", "").replace("_", "").isalnum():
            return "key 只允许字母/数字/下划线/连字符"
        return None

    # API 层调用 ↓

    def check_auth(self, secret: str, client_ip: str) -> Optional[str]:
        """返回拒绝原因或 None。"""
        if self.secret and secret != self.secret:
            return "密钥不匹配"
        if self.ip_allow and not any(
                (client_ip or "").startswith(p) for p in self.ip_allow):
            return f"IP {client_ip} 不在白名单"
        return None

    def fire(self, payload: dict):
        self._recv_count += 1
        meta = {"fired_by": "http"}
        for var, path in self.extract.items():
            meta[var] = ctx_get(payload or {}, path)
        self.emit_pulse(meta)

    def snapshot(self):
        return {"key": self.key, "auth": bool(self.secret),
                "ip_allow": self.ip_allow, "recv_count": self._recv_count}
