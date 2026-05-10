from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from backend.db.database import Base


class PluginRecord(Base):
    """已安装插件元数据。"""

    __tablename__ = "plugins"

    id = Column(Integer, primary_key=True, index=True)
    customer_code = Column(String(32), unique=True, index=True, nullable=False)
    name = Column(String(128), nullable=False)
    plugin_version = Column(String(64), nullable=False)
    tier = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="installed")
    is_active = Column(Boolean, nullable=False, default=False)
    install_path = Column(String(500), nullable=False)
    manifest_json = Column(Text, nullable=False)
    files_digest = Column(String(80), nullable=False)
    signed_by = Column(String(64), nullable=True)
    signed_at = Column(String(64), nullable=True)
    public_key_fingerprint = Column(String(64), nullable=True)
    installed_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PluginState(Base):
    """插件运行状态与最近错误。"""

    __tablename__ = "plugin_state"

    id = Column(Integer, primary_key=True, index=True)
    customer_code = Column(String(32), unique=True, index=True, nullable=False)
    runtime_status = Column(String(32), nullable=False, default="stopped")
    health = Column(String(32), nullable=False, default="unknown")
    last_error_code = Column(String(64), nullable=True)
    last_error_message = Column(Text, nullable=True)
    last_loaded_at = Column(DateTime(timezone=True), nullable=True)
    hook_success_count = Column(Integer, nullable=False, default=0)
    hook_error_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PluginAuditLog(Base):
    """插件安装/激活/加载失败等审计日志。"""

    __tablename__ = "plugin_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    customer_code = Column(String(32), index=True, nullable=True)
    action = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PluginConfigVersion(Base):
    """记录默认配置是否已经应用，避免重复覆盖客户本地配置。"""

    __tablename__ = "plugin_config_versions"

    id = Column(Integer, primary_key=True, index=True)
    customer_code = Column(String(32), index=True, nullable=False)
    plugin_version = Column(String(64), nullable=False)
    config_digest = Column(String(80), nullable=True)
    applied_at = Column(DateTime(timezone=True), server_default=func.now())
