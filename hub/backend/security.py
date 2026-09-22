"""Fleet Hub 安全原语: 口令 hash / 登录 token / 边缘 API Key 的 Fernet 加密。

结构对齐主程序 v3.10 (core/auth.py + v3.53 归档凭据 Fernet), 但代码独立
(枢纽不 import backend.*)。
"""
import hashlib
import hmac
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

# ============================================================
# 口令: PBKDF2-HMAC-SHA256 (登录低频, 迭代数拉高)
# ============================================================

_PBKDF2_ITER = 200_000


def hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), bytes.fromhex(salt), _PBKDF2_ITER)
    return f"pbkdf2${salt}${dk.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    try:
        _tag, salt, expected = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", plain.encode(),
                                 bytes.fromhex(salt), _PBKDF2_ITER)
        return hmac.compare_digest(dk.hex(), expected)
    except Exception:
        return False


# ============================================================
# 登录 token: 随机明文 + sha256 落库 (与边缘 API Key 同纪律)
# ============================================================

def generate_token() -> str:
    return f"ht_{secrets.token_urlsafe(32)}"


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


# ============================================================
# 边缘 API Key 加密: Fernet, 密钥文件落数据目录 (RFC 15 §8 hub_nodes.api_key)
# ============================================================

def _fernet(data_dir: Path) -> Fernet:
    key_file = data_dir / "hub_fernet.key"
    if not key_file.exists():
        key_file.write_bytes(Fernet.generate_key())
        try:
            key_file.chmod(0o600)
        except OSError:
            pass  # Windows 无 POSIX 权限位
    return Fernet(key_file.read_bytes())


def encrypt_api_key(plain: str, data_dir: Path) -> str:
    return _fernet(data_dir).encrypt(plain.encode()).decode()


def decrypt_api_key(enc: str, data_dir: Path) -> str:
    return _fernet(data_dir).decrypt(enc.encode()).decode()
