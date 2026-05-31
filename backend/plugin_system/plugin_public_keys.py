"""
插件签名公钥列表 — 由 scripts/plugin/inject-public-key.py 生成

⚠️ 严禁手工修改本文件
⚠️ 修改后必须 bump 主程序版本号 (见 design/02 §8.4)
"""
from __future__ import annotations


PLUGIN_PUBLIC_KEYS = [
    {
        "fingerprint": "d1f2fcb6fd620bab37e6e4c41d7f51d0",
        "valid_from":  "2026-05-30T14:31:15.455108+00:00",
        "valid_to":    "2030-05-30T14:31:15.455108+00:00",
        "comment":     "v3.14 dev 主签名公钥 (本地开发用, 非生产)",
        "pem":         b"""
        -----BEGIN PUBLIC KEY-----
        MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAwxb9FISylArHnEXul4I1
        Am/gSBEpnV3zCT6hTtznCkVRHoT0f/CVo0Atu2CyedPuJ/4zcOh36tYmMJByQ5Wh
        vrwqv7IXUNxOBxI/86p2ZTsiCZSK0OlYeKZX84vEtCA1saNBX0jQWvkN4vaENECn
        EoQ0FKgX7GP0TLeBx9gc6RoTalZyDF+GHs1fyaM3da4vmOSf0hQ3PouOJj98VtOI
        9lzOc301igHG0YiPaVImNHfbY96c3LpCe3RmW54d0pQSa99DVYl83pl+G0ZSbAHB
        M25mxkIAPtZW12ZdnxGB8OX3uuOunMU1UlPL+wHPsMMRfbtMpGwlYw57+y819PIP
        /FkXIhhBFJbJeTymiWdUhiciWSaX5NMANclbnXvVQ6FSWA0xfw3EmmBDFvht1QAA
        mfUQr9Lt0k6xGnMKibmI0d32AIRZKXyrLafAQX6id5dP76DVW9UV4AxLmm8B40Pp
        RSk/sNI1EVHODEkC4TjU8hEI1Yw+Vozp5fO6xwC+fmKiNb/UU4WTXLtt8lJrtWDa
        zl400aEJQX2BBHzxZqn50MyK4/9gJkXufPb0mC7h4u6aLGPiL+cPGV1vJBgCxyJR
        AdIV7ad92SkSVkl7ieWqFA5H9u4qW+TgChtOXQ6CLaDtqZx0waWAXPgUrJgJrGGl
        z4vGl3VX65wF5YqawWifTc8CAwEAAQ==
        -----END PUBLIC KEY-----
""",
    },
]

def get_pubkey_by_fingerprint(fp_hex: str) -> dict | None:
    for entry in PLUGIN_PUBLIC_KEYS:
        if entry["fingerprint"].lower() == fp_hex.lower():
            return entry
    return None
