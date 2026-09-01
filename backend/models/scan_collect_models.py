"""
周期多码采集（scan collect）ORM — v3.56

场景：装配工位一个工件周期内要扫多个分类码（如 6~9 个芯子码 + 1 母排码
+ 1 模具盖板码 + 1 个字母开头的工件码收尾），少扫/重扫报 NG，
已扫码实时显示在检测主页，按项目配置槽位数量（切项目=切作业）。

设计要点：
- 配置**按项目**存（scan_collect_configs.project_id 唯一），编辑 UI 在
  MES管理→扫码器，但数据随项目切换 —— 满足"扫 9 次还是 12 次跟着作业走"。
  刻意不进 Project.pipeline_config：Project 页 handleSaveProject 是白名单
  重组 pipeline_config，陌生键保存即丢（v3.55 实测），独立表彻底避开。
- 逐码记录落 scan_collect_records，结算后回填 workpiece_id / group_result，
  供工件追溯页反查"这个工件绑了哪些组件码"。
- 新表走 Base.metadata.create_all 自动创建，无需 ALTER 迁移；
  接入三处注册：backend/main.py 显式 import + tests/conftest.py + alembic/env.py。
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, String, JSON, Index,
)

from backend.db.database import Base


class ScanCollectConfig(Base):
    """周期多码采集配置 — 一行 = 一个项目的采集规格。

    config JSON 结构（全部键可缺省，engine 侧 .get 兜底）::

        {
          "slots": [                       # 槽位定义，按数组顺序做顺序兜底分类
            {"key": "busbar", "label": "母排码", "count": 1, "regex": ""},
            {"key": "cover",  "label": "盖板码", "count": 1, "regex": ""},
            {"key": "chip",   "label": "芯子码", "count": 6, "regex": ""},
            {"key": "workpiece", "label": "工件码", "count": 1,
             "regex": "^[A-Za-z]+[0-9]+$", "role": "closing"}   # closing=收尾码
          ],
          "sequence_fallback": true,       # 正则不中时按槽位顺序"依次填坑"
          "dedup_in_group": "ng_alarm",    # 本组内重复码: ng_alarm | reject
          "dedup_cross_group": "off",      # 跨工件历史重复: off | reject | ng_alarm
          "settle_on": "closing",          # 结算时机: closing(扫收尾码) | all_filled
          "on_overflow": "reject",         # 槽位超量: reject | ng_alarm
          "on_unmatched": "reject",        # 无法归类的码: reject | ng_alarm
          "timeout_sec": 0,                # >0: 组内最后一扫后超时未收尾 → NG 结算
          "event_ok_id": 1,                # 结算 OK 借用的事件 id
          "event_ng_id": 2                 # 结算 NG / ng_alarm 策略借用的事件 id
        }
    """
    __tablename__ = "scan_collect_configs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, nullable=False, unique=True, index=True)
    enabled = Column(Boolean, default=False, nullable=False)
    config = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ScanCollectRecord(Base):
    """逐码记录 — 一行 = 一次被接收的扫码（拒收的码只进 scan_logs 不进本表）。

    group_id 标识一个"码组"（≈一个工件的采集周期）；结算后整组回填
    group_result / workpiece_id / settled_at。纠错删除的码 status='deleted'
    留痕不物理删。
    """
    __tablename__ = "scan_collect_records"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(String(64), nullable=False, index=True)
    channel_id = Column(Integer, nullable=False, default=0)
    project_id = Column(Integer, nullable=False, index=True)
    slot_key = Column(String(64), nullable=False)
    slot_label = Column(String(128), nullable=True)
    code = Column(String(256), nullable=False, index=True)
    seq = Column(Integer, nullable=False, default=0)     # 组内扫码顺序 1-based
    status = Column(String(16), nullable=False, default="scanned")  # scanned/deleted/void
    group_result = Column(String(32), nullable=True)     # ok / ng_missing / ng_dup / ng_timeout
    workpiece_id = Column(Integer, nullable=True, index=True)
    scanned_at = Column(DateTime, default=datetime.now)
    settled_at = Column(DateTime, nullable=True)


Index("idx_scan_collect_records_proj_code",
      ScanCollectRecord.project_id, ScanCollectRecord.code)
