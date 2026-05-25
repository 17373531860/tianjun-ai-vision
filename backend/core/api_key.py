"""
M2M API Key 工具 — 生成 / hash / 校验 + FastAPI 依赖工厂.

与 core/auth.py 的"用户 token"体系完全独立:
  - 用户 token  → bcrypt + 内存缓存 + DB 落盘, 绑 User
  - API Key    → sha256 + DB 查表(M2M 调频高, 加 LRU-ish 缓存), 绑 scope

为啥不直接复用 SessionToken?
  - SessionToken 必须绑 user_id; M2M 没有"用户"概念
  - SessionToken 用 bcrypt 太慢, 副机心跳每秒会扛不住
  - 权限模型不同: SessionToken 走 User.roles → permissions;
    API Key 走 scope 直接匹配端点
"""
import hashlib
import secrets
import time
from typing import Optional, Dict

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.core.auth_deps import is_auth_enabled
from backend.db.database import get_db
from backend.models.auth_models import APIKey


# ============================================================
# 生成 / hash
# ============================================================

KEY_PLAINTEXT_PREFIX = "tk_"   # token key 前缀, 让 key 在日志里好识别
PREFIX_DISPLAY_LEN = 10        # 列表展示前 10 个字符


def generate_api_key() -> str:
    """生成一个 url-safe 的 API Key 明文.

    格式: tk_<43 字符 url-safe base64>  总长约 46 字符
    熵: 32 字节 ≈ 256 bit
    """
    body = secrets.token_urlsafe(32)
    return f"{KEY_PLAINTEXT_PREFIX}{body}"


def hash_api_key(plaintext: str) -> str:
    """sha256 hex digest. 选 sha256 不选 bcrypt 是为 M2M 高频调用性能.
    Key 明文已 256 bit 随机, 不需要抗暴力 hash."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def extract_prefix(plaintext: str) -> str:
    """取 key 的前 N 个字符做 UI 展示用 (tk_xxxxxxx)"""
    return plaintext[:PREFIX_DISPLAY_LEN]


# ============================================================
# 缓存 (hash → (id, scope, enabled, expires_ts))
# ============================================================

# 简单内存缓存. 频繁的 M2M 调用避免每次查表; CRUD 时调 invalidate_cache().
# 不做 LRU, M2M key 通常 < 100 条.
_KEY_CACHE: Dict[str, Optional[dict]] = {}


def invalidate_cache() -> None:
    _KEY_CACHE.clear()


def _load_key(db: Session, key_hash: str) -> Optional[dict]:
    """缓存 + 查表; 命中后返回 dict 简化结构 (避免持有 ORM 对象跨 session)"""
    if key_hash in _KEY_CACHE:
        return _KEY_CACHE[key_hash]

    row = db.query(APIKey).filter(APIKey.key_hash == key_hash).first()
    if not row:
        _KEY_CACHE[key_hash] = None
        return None

    info = {
        "id": row.id,
        "scope": row.scope or "*",
        "enabled": bool(row.enabled),
        "expires_ts": (row.expires_at.timestamp() if row.expires_at else None),
    }
    _KEY_CACHE[key_hash] = info
    return info


def _bump_use_count(db: Session, key_hash: str) -> None:
    """每次 verify 成功后 ++use_count + 写 last_used_at. 单独函数好被关掉.
    用直接 SQL UPDATE 避免拉一遍对象再 commit, M2M 高频要轻."""
    from sqlalchemy import update
    from sqlalchemy.sql import func
    try:
        db.execute(
            update(APIKey).where(APIKey.key_hash == key_hash).values(
                use_count=APIKey.use_count + 1,
                last_used_at=func.now(),
            )
        )
        db.commit()
    except Exception:
        db.rollback()


# ============================================================
# scope 匹配
# ============================================================

def match_scope(required: str, granted: str) -> bool:
    """与 permissions.match_permission 类似的轻量匹配 (单 vs 单):
      granted="*"           → 永远 match
      granted=required      → 精确
      granted="cluster.*"   → 后缀通配 (预留)
    """
    if not granted:
        return False
    if granted == "*":
        return True
    if granted == required:
        return True
    if granted.endswith(".*"):
        prefix = granted[:-2]
        if required == prefix or required.startswith(prefix + "."):
            return True
    return False


# ============================================================
# Header 提取
# ============================================================

def _extract_api_key(request: Request) -> Optional[str]:
    """优先 X-API-Key header; 兼容 Authorization: ApiKey <key> 形式"""
    h = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    if h:
        return h.strip() or None
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("apikey "):
        return auth[7:].strip() or None
    return None


# ============================================================
# 依赖工厂
# ============================================================

def require_api_key(scope: str):
    """生成一个 FastAPI 依赖: 校验请求里的 X-API-Key 且 key.scope 覆盖 scope.

    auth_enabled=false 时 → 放行 (与现有客户单机部署体验零差异)
    auth_enabled=true 时:
      - 缺 header → 401
      - 找不到 key → 403
      - 已禁用 → 403
      - 已过期 → 403
      - scope 不匹配 → 403

    用法:
      @router.post("/cluster/report",
                   dependencies=[Depends(require_api_key("cluster"))])
      def receive_report(...): ...
    """
    def _dep(request: Request, db: Session = Depends(get_db)):
        if not is_auth_enabled(db):
            return None  # 关闭鉴权 = M2M 完全放行

        plaintext = _extract_api_key(request)
        if not plaintext:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="此 M2M 端点需要 API Key (Header: X-API-Key)",
            )

        h = hash_api_key(plaintext)
        info = _load_key(db, h)
        if not info:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="API Key 无效")
        if not info["enabled"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="API Key 已禁用")
        if info["expires_ts"] is not None and time.time() > info["expires_ts"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="API Key 已过期")
        if not match_scope(scope, info["scope"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API Key scope 不足 (需要: {scope}, 实际: {info['scope']})",
            )
        _bump_use_count(db, h)
        return info
    return _dep
