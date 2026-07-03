"""称重投料模式 (logic_mode='weighing') 持久化模型。

终验收 6.1 要求"每件逐件记录初值/终值/料别/判定/时间并上传"。逐件逐料记录必须落库
才能长期可追溯 (内存台账重启即失)。一行 = 一件产品的一道料的一次称量结果。

字段与引擎产出的 result dict 对齐 (backend/services/weighing_engine.py)。
新表, 启动 create_all 自动建, 无需 ALTER。
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from sqlalchemy.sql import func
from backend.db.database import Base


class WeighingRecord(Base):
    """逐件逐料称重记录 (6.1 台账)。"""
    __tablename__ = "weighing_records"

    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(Integer, nullable=False, default=0, index=True)
    product_sn = Column(String(128), nullable=True, index=True)   # 产品序列号 (扫码)
    model_name = Column(String(128), nullable=True)               # 水泥型号
    operator = Column(String(64), nullable=True)                  # 操作人员
    material = Column(String(64), nullable=True)                  # 料别 (钢帽/钢脚水泥)
    standard = Column(Float, nullable=True)                       # 标准量 kg (未配标准为 NULL)
    initial = Column(Float, nullable=True, default=0.0)           # 初值 kg (去皮后=0)
    net = Column(Float, nullable=False, default=0.0)              # 终值=净投料量 kg
    verdict = Column(String(16), nullable=False, default="", index=True)  # ok/shortage/over/no_spec
    ts = Column(Float, nullable=True)                             # 业务时间戳 (epoch, 引擎判定时刻)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


Index("ix_weighing_records_ch_created", WeighingRecord.channel_id, WeighingRecord.created_at)
