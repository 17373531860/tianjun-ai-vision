# -*- coding: utf-8 -*-
"""归档目的地凭据加密 (v3.53 四期)。

远端 adapter (FTP/SFTP/S3/HTTP) 的密码/密钥不能明文落库:
- 首次使用时在 {DATA_DIR}/archive_secret.key 生成机器本地 Fernet 密钥 (0600)
- dest_config 里的敏感字段 (password/secret/token 类 key) 写库前加密为
  "enc:<token>" 前缀串, adapter 使用前解密
- 密钥只存本机 → 配置导出/DB 拷走不泄漏凭据 (换机需重填凭据, 可接受)
"""
import os
import threading
from typing import Any, Dict, Optional

from backend.core.config import DATA_DIR

_KEY_FILE = os.path.join(DATA_DIR, "archive_secret.key")
_ENC_PREFIX = "enc:"
# dest_config 里视为敏感、需要加密的字段名
SENSITIVE_KEYS = {"password", "secret", "secret_key", "token",
                  "access_token", "passphrase", "api_key"}

_lock = threading.Lock()
_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    from cryptography.fernet import Fernet
    with _lock:
        if _fernet is not None:
            return _fernet
        if os.path.exists(_KEY_FILE):
            with open(_KEY_FILE, "rb") as f:
                key = f.read().strip()
        else:
            key = Fernet.generate_key()
            fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
        _fernet = Fernet(key)
        return _fernet


def encrypt_value(plain: str) -> str:
    """明文 → enc:token。已加密的原样返回 (幂等)。"""
    if plain is None or plain == "" or str(plain).startswith(_ENC_PREFIX):
        return plain
    token = _get_fernet().encrypt(str(plain).encode("utf-8")).decode("ascii")
    return _ENC_PREFIX + token


def decrypt_value(stored: str) -> str:
    """enc:token → 明文。非加密串原样返回 (兼容手填明文)。"""
    if not stored or not str(stored).startswith(_ENC_PREFIX):
        return stored
    from cryptography.fernet import InvalidToken
    try:
        return _get_fernet().decrypt(
            str(stored)[len(_ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception):
        raise ValueError("凭据解密失败 (密钥文件可能已更换, 请重填凭据)")


def encrypt_config(cfg: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """dest_config 落库前: 敏感字段全部加密 (幂等)。"""
    if not cfg:
        return cfg
    out = dict(cfg)
    for k, v in out.items():
        if k.lower() in SENSITIVE_KEYS and isinstance(v, str) and v:
            out[k] = encrypt_value(v)
    return out


def decrypt_config(cfg: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """adapter 使用前: 敏感字段解密。"""
    if not cfg:
        return cfg
    out = dict(cfg)
    for k, v in out.items():
        if k.lower() in SENSITIVE_KEYS and isinstance(v, str) and v:
            out[k] = decrypt_value(v)
    return out


def mask_config(cfg: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """API 返回给前端时: 敏感字段一律打码 (前端提交 '******' 表示不改)。"""
    if not cfg:
        return cfg
    out = dict(cfg)
    for k, v in out.items():
        if k.lower() in SENSITIVE_KEYS and v:
            out[k] = "******"
    return out


MASK_PLACEHOLDER = "******"


def merge_masked_config(new_cfg: Optional[Dict[str, Any]],
                        old_cfg: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """前端回传的配置里敏感字段是 '******' 时, 保留库里旧值 (未改语义)。"""
    if not new_cfg:
        return new_cfg
    out = dict(new_cfg)
    for k, v in out.items():
        if (k.lower() in SENSITIVE_KEYS and v == MASK_PLACEHOLDER
                and old_cfg and k in old_cfg):
            out[k] = old_cfg[k]
    return out
