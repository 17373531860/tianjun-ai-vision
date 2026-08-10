"""
PLC 连接器数据模型 (RFC 13, v3.48)

一行 = 一条 PLC 连接（一台 PLC 的一个通讯会话）。
点位表 / 触发规则 / 写回规则全部是 JSON 列——先例是 Project 的 7 个 JSON 配置字段。
客户的整套对接方案就是这一行配置，可经 /api/v1/plc/connections/{id}/export 导出复制。

JSON 结构约定（详见 docs/plugin-system/design/13_plc_connector_rfc.md 第四节实例）:

conn_params: 各 driver 自定义, 如 s7: {"ip","rack","slot","port","poll_interval_ms"}
points: [{"key","addr","type","dir","byte_order","encoding","strip","length",
          "bit","scale","offset","poll_interval_ms"}]
read_rules: [{"name","when":[{"point","trigger","value","min_ms"}],
              "debounce_ms","min_interval_ms","ack_mode","ack_point","ack_value",
              "clear_after_ms","stuck_timeout_s","stuck_action","channel",
              "actions":[{"do", ...}]}]
write_rules: [{"on","period_ms","channels","results","writes":
               [{"point","value","value_map","source","reset_after_ms","reset_value"}]}]
options: {"default_channel","reconnect_initial_s","reconnect_max_s",
          "heartbeat_watch":{"point","timeout_s","action","event"},
          "on_disconnect":{"alarm_event","pause_detection"}}
"""
from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, func

from backend.db.database import Base


class PLCConnection(Base):
    """PLC 连接配置表 — 通用 PLC 对接（协议/点位/规则全配置化）"""
    __tablename__ = "plc_connections"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), nullable=False)
    # driver: s7 / modbus_tcp / modbus_rtu / mc / fins / ethernet_ip / opcua / mock
    # （注册表在 backend/services/plc/drivers/__init__.py, 插件可扩展）
    driver = Column(String(32), nullable=False, default="s7")
    enabled = Column(Boolean, nullable=False, default=False)

    conn_params = Column(JSON, nullable=True)
    points = Column(JSON, nullable=True)
    read_rules = Column(JSON, nullable=True)
    write_rules = Column(JSON, nullable=True)
    options = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())
