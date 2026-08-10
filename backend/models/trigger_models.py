"""RFC 14 统一触发中心: trigger_channels 表 (一行一触发源实例)。

结构对照 plc_connections (RFC 13): 类型 + 参数/规则/选项三个 JSON 列。
纯新表由启动 create_all 自建, 老库升级自动补表, 无需 ALTER 迁移。
"""
from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, func

from backend.db.database import Base


class TriggerChannel(Base):
    __tablename__ = "trigger_channels"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), nullable=False)
    # pixel_region / hid_key / http / serial_pattern / timer / mock / (插件注册)
    type = Column(String(32), nullable=False, default="pixel_region")
    enabled = Column(Boolean, nullable=False, default=False)
    # 触发源类型参数 (各类型 schema 见 sources/ 对应模块 docstring)
    params = Column(JSON, nullable=True)
    # 触发规则: [{when/debounce_ms/min_interval_ms/active_window/actions}]
    rules = Column(JSON, nullable=True)
    # 杂项: default_channel / 历史条数等
    options = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())
