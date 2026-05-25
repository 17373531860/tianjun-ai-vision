"""
用户系统数据模型 (v3.10.0+)

四张表：
- User           账号 (含 password_hash, 关联到 Role 多对多)
- Role           角色 (含 permissions JSON 列表 + is_builtin 内置三角色保护)
- UserRole       多对多关联表 (一个用户可有多角色, 权限取并集)
- SessionToken   登录会话 token (内存缓存 + 落盘备份, 重启不丢)

设计原则：
1. auth_enabled 总开关存在 SystemConfig (KV 表 key="auth.enabled"), 默认 false
2. auth_enabled=false 时所有请求伪装最高管理员 (与现有客户体验零差异)
3. auth_enabled=true 时, 默认匿名身份 = operator 角色 (工人开机不用打字)
4. 想做配置 → 顶栏点登录, 切到工程师/管理员
5. 权限粒度: 端点级 permission key + 通配符 (admin = ["*"])

与旧 operators 表完全无关 — 旧表保留在老库做"班组工人名册"留存, 新系统是
独立鉴权主体, 数据归属字段 (detection_sessions.operator_id) 后续重用为 user_id.
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, JSON, ForeignKey,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.db.database import Base


# ============================================================
# 账号
# ============================================================

class User(Base):
    """账号 — 鉴权主体, 不等同于旧 operator (员工名册)"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    password_hash = Column(String(128), nullable=False)  # bcrypt hash, 含 salt
    display_name = Column(String(64), nullable=True)  # UI 显示名, 不填回退 username
    active = Column(Boolean, default=True, nullable=False, index=True)
    must_change_password = Column(Boolean, default=False, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    user_roles = relationship("UserRole", back_populates="user",
                              cascade="all, delete-orphan")


# ============================================================
# 角色
# ============================================================

class Role(Base):
    """角色 — 一组 permission key 的集合, 支持通配符 *

    permissions 字段示例:
      admin:    ["*"]                                    全部
      engineer: ["project.*", "monitor.view", ...]       项目模块全部 + 看 monitor
      operator: ["monitor.view", "monitor.detection.control"]
    """
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    # 角色代码: admin / engineer / operator / 客户自定义
    code = Column(String(32), unique=True, index=True, nullable=False)
    name = Column(String(64), nullable=False)  # 显示名 "超级管理员"
    description = Column(String(255), nullable=True)
    # 权限列表 (字符串列表), 通配符匹配在 core/permissions.py
    permissions = Column(JSON, nullable=False, default=list)
    # 内置三角色禁删 (UI 只能改 permissions, 删按钮置灰)
    is_builtin = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    user_roles = relationship("UserRole", back_populates="role",
                              cascade="all, delete-orphan")


# ============================================================
# 用户角色关联
# ============================================================

class UserRole(Base):
    """User <-> Role 多对多关联"""
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_role"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="user_roles")
    role = relationship("Role", back_populates="user_roles")


# ============================================================
# 会话 Token
# ============================================================

class SessionToken(Base):
    """登录会话 token — 落盘备份, 启动时回填到 core/auth.py 的内存缓存

    生命周期:
      login 成功 → 生成 token → 写表 + 写内存 dict
      每次 get_current_user → 内存 dict 优先, miss 兜底查表
      logout / 过期 → 删表 + 删内存
      重启后 → 启动钩子 load_tokens_from_disk() 把未过期的 token 装回内存
    """
    __tablename__ = "session_tokens"

    id = Column(Integer, primary_key=True, index=True)
    # secrets.token_hex(32) → 64 char hex string
    token = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # NULL = 长会话不过期 (工控机场景常态); 未来可设过期实现自动登出
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_used_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


Index("ix_session_tokens_user_expires",
      SessionToken.user_id, SessionToken.expires_at)


# ============================================================
# API Key (M2M 鉴权)
# ============================================================

class APIKey(Base):
    """M2M API Key — 给副机、外部 MES、Electron IPC 等"机器对机器"通信用.

    与 SessionToken 的差异:
      - 不绑定 User, 不带 roles, 只有 scope 字段做粗粒度访问控制
      - 用 sha256 hash (不用 bcrypt) — M2M 调用高频 (副机心跳每秒),
        bcrypt 一次 ~100ms 性能扛不住; key 本身 32B 随机, 不需要抗暴力
      - 创建时仅在 API 响应里返回明文一次, 之后只能看 key_prefix
      - 适用 auth_enabled=true 场景下放行 M2M 端点; auth_enabled=false 时
        所有 M2M 端点本来就放行 (与现有体验零差异)

    scope 取值:
      "*"             所有 M2M 端点 (慎用)
      "cluster"       副机 report / heartbeat
      "mes.receive"   外部 MES 推工单
      "license.cache" Electron IPC 写 License 缓存
    """
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    # key 前 8 位明文, 用于 UI 列表识别 (如 "tk_a3f9b2..." → 前缀 "tk_a3f9b2")
    key_prefix = Column(String(16), nullable=False, index=True)
    # sha256(key) 的 64 char hex
    key_hash = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)  # 人类可读名 "副机-工位1"
    scope = Column(String(64), nullable=False, default="*")
    description = Column(String(255), nullable=True)
    enabled = Column(Boolean, default=True, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    use_count = Column(Integer, default=0, nullable=False)
    # NULL = 永不过期 (工控机常态); 未来可设过期时间
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
