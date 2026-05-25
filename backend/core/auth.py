"""
鉴权核心 — 密码哈希 + token 生成 + 内存缓存 + 落盘

设计:
- 密码用 bcrypt 直接哈希, 不走 passlib (因为 passlib 1.7.4 与 bcrypt 5.x
  之间有 __about__ 兼容警告; 直接用 bcrypt 库更稳)
- Token 用 secrets.token_hex(32) 生成 256-bit 随机串
- 内存 dict 加锁缓存 token → user_id, 失败兜底查 session_tokens 表
- 启动时 load_tokens_from_disk() 把表里所有未过期 token 装回内存

v3.10+ 阶段 4 新增:
- "当前活跃用户" 落盘 current_user.json (单机一个登录者)
  login 写, logout/disable-auth 清; 检测主循环 (工作线程) 读取
  → 给 DetectionSession/DetectionCycle.operator_id 列写值
"""
import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, Optional

import bcrypt
import secrets as _secrets

from backend.core.config import DATA_DIR
from backend.db.database import SessionLocal
from backend.models.auth_models import SessionToken


# ============================================================
# 密码哈希
# ============================================================

def hash_password(password: str) -> str:
    """bcrypt 哈希 (12 rounds, ~250ms 单次, 防暴力).

    返回的字符串自带 salt, 直接存数据库即可.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("密码不能为空")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码与 bcrypt 哈希.

    出错一律返回 False, 不抛异常 (避免 timing attack 或 hash 损坏导致 500).
    """
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except Exception:
        return False


# ============================================================
# Token 内存缓存
# ============================================================

# token (str) → user_id (int)
_token_cache: Dict[str, int] = {}
_cache_lock = threading.Lock()


def generate_token() -> str:
    """生成 256-bit 随机 token (64 字符 hex)"""
    return _secrets.token_hex(32)


def cache_token(token: str, user_id: int) -> None:
    """写入内存缓存"""
    with _cache_lock:
        _token_cache[token] = user_id


def uncache_token(token: str) -> None:
    """从内存缓存移除"""
    with _cache_lock:
        _token_cache.pop(token, None)


def resolve_token_cached(token: str) -> Optional[int]:
    """从内存查 user_id; 不查库 (调用方需要时自行查库兜底)"""
    if not token:
        return None
    with _cache_lock:
        return _token_cache.get(token)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
# Token 持久化 (落盘)
# ============================================================

def load_tokens_from_disk() -> int:
    """启动时调用: 把 session_tokens 表里所有未过期的 token 装回内存缓存.

    返回成功装载的条数.
    """
    now = _now_utc()
    db = SessionLocal()
    count = 0
    try:
        rows = db.query(SessionToken).all()
        for row in rows:
            if row.expires_at is not None and row.expires_at < now:
                continue
            with _cache_lock:
                _token_cache[row.token] = row.user_id
            count += 1
        print(f"[Auth] 启动恢复 {count} 个有效 token")
    except Exception as e:
        print(f"[Auth] token 启动恢复失败 (忽略): {e}")
    finally:
        db.close()
    return count


def persist_token(token: str, user_id: int,
                  expires_at: Optional[datetime] = None) -> None:
    """token 写库 (落盘备份)"""
    db = SessionLocal()
    try:
        row = SessionToken(token=token, user_id=user_id, expires_at=expires_at)
        db.add(row)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Auth] token 写库失败: {e}")
    finally:
        db.close()


def delete_token(token: str) -> None:
    """logout: 删库 + 删内存"""
    uncache_token(token)
    if not token:
        return
    db = SessionLocal()
    try:
        db.query(SessionToken).filter(SessionToken.token == token).delete()
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Auth] token 删除失败: {e}")
    finally:
        db.close()


def delete_all_tokens_for_user(user_id: int) -> None:
    """删除某个用户的所有 token (改密码 / 禁用账号时调)"""
    if user_id is None:
        return
    db = SessionLocal()
    try:
        rows = db.query(SessionToken).filter(SessionToken.user_id == user_id).all()
        for r in rows:
            uncache_token(r.token)
        db.query(SessionToken).filter(SessionToken.user_id == user_id).delete()
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Auth] 删除用户 {user_id} 所有 token 失败: {e}")
    finally:
        db.close()


# ============================================================
# 当前活跃用户 (落盘) — v3.10+ 阶段 4
# 给检测主循环 (工作线程) 写 DetectionSession/Cycle.operator_id 列用.
# 设计原则:
# - 客户机一台机一个登录用户, 故只记单值, 不按 channel 分键
# - login 时写, logout / disable-auth 时清空
# - 鉴权关闭 (默认) → 文件不应存在 → 读返回 None → cycle 写 NULL
# - 进程重启不丢 (文件保留)
# ============================================================

_CURRENT_USER_PATH = os.path.join(DATA_DIR, "current_user.json")
_current_user_lock = threading.Lock()


def set_current_active_user(user_id: Optional[int]) -> None:
    """记录 / 清空当前活跃登录用户. None 表示清空 (logout / disable-auth)."""
    try:
        os.makedirs(os.path.dirname(_CURRENT_USER_PATH), exist_ok=True)
        with _current_user_lock:
            if user_id is None:
                if os.path.exists(_CURRENT_USER_PATH):
                    os.remove(_CURRENT_USER_PATH)
            else:
                tmp = _CURRENT_USER_PATH + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"user_id": int(user_id)}, f)
                os.replace(tmp, _CURRENT_USER_PATH)
    except Exception as e:
        print(f"[Auth] current_user 落盘失败 (忽略): {e}", flush=True)


def get_current_user_id() -> Optional[int]:
    """当前登录用户 id (用于检测数据归属 / MES / 导出渲染).

    返回:
    - 鉴权关闭 (默认) → None (文件不存在)
    - 鉴权启用 + 无登录 (匿名 operator) → None
    - 鉴权启用 + 有真实登录 → user.id (整数)

    线程安全: 落盘文件读 + lock 保护, 工作线程可调.
    """
    try:
        with _current_user_lock:
            if not os.path.exists(_CURRENT_USER_PATH):
                return None
            with open(_CURRENT_USER_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        uid = data.get("user_id")
        return int(uid) if uid is not None else None
    except Exception:
        return None
