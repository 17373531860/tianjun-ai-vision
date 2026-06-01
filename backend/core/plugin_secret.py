"""
插件客户绑定 HMAC 密钥 (PLUGIN_SECRET) — 全网统一固定密钥

设计依据: docs/plugin-system/design/02_signature.md §1.3 / §5.2
- 主程序验签时需要它来校验 customer_hmac, 因此必须内置在主程序里.
- 安全模型 (§10.4) 已明确接受它可被反编译获取: 真正的防伪底线是 RSA 私钥
  (永不进主程序), HMAC 只是"防中级攻击者换 customer_code 复用插件"的第二道锁.

加载优先级:
1. 环境变量 PLUGIN_SECRET_HEX (CI/部署可覆盖, 见 §5.2)
2. 内置默认 (开箱即用, 正式安装包无需额外配置)

⚠️ 更换本密钥会使"所有已签插件"失效 (需重新签名), 非必要不要改.
"""
from __future__ import annotations

import base64
import os

# 全网统一密钥的内置默认值 (base64, 解码后 32 byte)
_DEFAULT_B64 = "JBXujSDZbId7YQ5a+nJtxGvqyUsGEnbGR8Bk3eLDMM4="


def _load() -> bytes:
    hex_env = os.environ.get("PLUGIN_SECRET_HEX", "").strip()
    if hex_env:
        secret = bytes.fromhex(hex_env)
        if len(secret) == 32:
            return secret
    secret = base64.b64decode(_DEFAULT_B64)
    if len(secret) != 32:
        raise RuntimeError("PLUGIN_SECRET 内置默认值非 32 byte")
    return secret


PLUGIN_SECRET: bytes = _load()
