"""LG 工时看板 — 插件自有 ORM 表。

约定 (design/06 §五):
  - 表名前缀 p_lg_worktime_ (customer_code 连字符转下划线)
  - 类名 Plugin 前缀
  - 继承主程序 Base, 由 registry.tables.register() 建表 (checkfirst)
"""
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, UniqueConstraint
from sqlalchemy.sql import func

from backend.db.database import Base


class PluginLgWorktimeCycleLean(Base):
    """每周期 LEAN 价值分解 — cycle_end 时按当时的步骤价值配置冻结落库。

    冻结语义: 配置后续变更不回写历史, 看板/导出的历史口径永远是"当时怎么算的"。
    等待时间 (步骤间隔) 全部归 NVA (LG 原系统口径), 单独存 wait_seconds 便于看板拆项。
    """

    __tablename__ = "p_lg_worktime_cycle_lean"
    __table_args__ = (UniqueConstraint("cycle_id", name="uq_lgwt_lean_cycle_id"),)

    id = Column(Integer, primary_key=True, index=True)
    cycle_id = Column(Integer, index=True, nullable=False, unique=True)
    session_id = Column(Integer, index=True, nullable=True)
    channel_id = Column(Integer, index=True, nullable=True)
    project_id = Column(Integer, index=True, nullable=True)
    cycle_number = Column(Integer, nullable=True)
    is_good = Column(Boolean, nullable=True)
    cycle_duration = Column(Float, nullable=True)      # 周期总时长(秒), 来自主程序 cycle.duration
    va_seconds = Column(Float, nullable=False, default=0.0)    # 增值动作耗时
    bva_seconds = Column(Float, nullable=False, default=0.0)   # 必要非增值动作耗时
    nva_seconds = Column(Float, nullable=False, default=0.0)   # 非增值动作耗时(步骤级, 不含等待)
    wait_seconds = Column(Float, nullable=False, default=0.0)  # 步骤间等待(归 NVA)
    end_time = Column(DateTime, index=True, nullable=True)     # 周期结束时刻(看板按日聚合用)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
