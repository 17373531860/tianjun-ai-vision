"""插件自有 ORM 表示例。

注意：真实插件应 import 主程序暴露的 Base，而不是自己创建 declarative_base。
这里为了示例可读性使用字符串注释，实施时按 design/06 的 PluginHost 注入方式替换。
"""

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from backend.db.database import Base


class PluginInspectionNote(Base):
    """插件表必须 p_{customer_code}_* 命名，ORM 类必须 Plugin 前缀。"""

    __tablename__ = "p_internal_demo_notes"

    id = Column(Integer, primary_key=True, index=True)
    cycle_id = Column(Integer, index=True, nullable=True)
    barcode = Column(String(128), index=True, nullable=True)
    result = Column(String(32), nullable=False, default="UNKNOWN")
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
