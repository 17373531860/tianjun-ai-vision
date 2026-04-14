"""
MES 系统数据模型
工单管理、工件追溯、缺陷记录、扫码器配置、外部 MES 对接
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float, Text, JSON,
    ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.db.database import Base


# ============================================================
# 工单管理
# ============================================================

class WorkOrder(Base):
    """工单表 — 生产工单全生命周期管理

    状态机: draft -> pending -> in_progress -> completed
                             -> paused -> in_progress
                             -> cancelled
    """
    __tablename__ = "work_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(64), unique=True, nullable=False, index=True)
    external_id = Column(String(128), nullable=True, index=True)

    product_name = Column(String(128), nullable=False)
    product_code = Column(String(64), nullable=True)
    product_spec = Column(String(256), nullable=True)

    planned_qty = Column(Integer, nullable=False, default=0)
    completed_qty = Column(Integer, nullable=False, default=0)
    good_qty = Column(Integer, nullable=False, default=0)
    ng_qty = Column(Integer, nullable=False, default=0)
    rework_qty = Column(Integer, nullable=False, default=0)
    scrap_qty = Column(Integer, nullable=False, default=0)
    yield_rate = Column(Float, nullable=True)

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    priority = Column(Integer, nullable=False, default=3)
    status = Column(String(20), nullable=False, default="draft", index=True)
    source = Column(String(20), nullable=False, default="manual")

    planned_start = Column(DateTime(timezone=True), nullable=True)
    planned_end = Column(DateTime(timezone=True), nullable=True)
    actual_start = Column(DateTime(timezone=True), nullable=True)
    actual_end = Column(DateTime(timezone=True), nullable=True)

    customer_name = Column(String(128), nullable=True)
    remark = Column(Text, nullable=True)
    extra_data = Column(JSON, nullable=True)
    created_by = Column(String(64), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    project = relationship("Project", foreign_keys=[project_id])
    batches = relationship("Batch", back_populates="order", cascade="all, delete-orphan")
    workpieces = relationship("Workpiece", back_populates="order")

    # 合法的状态变迁
    VALID_TRANSITIONS = {
        "draft":       ["pending", "cancelled"],
        "pending":     ["in_progress", "cancelled"],
        "in_progress": ["paused", "completed", "cancelled"],
        "paused":      ["in_progress", "cancelled"],
        "completed":   [],
        "cancelled":   [],
    }


class Batch(Base):
    """批次表"""
    __tablename__ = "batches"
    __table_args__ = (
        UniqueConstraint("order_id", "batch_no", name="uq_order_batch"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_no = Column(String(64), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False)

    material_lot = Column(String(128), nullable=True)
    planned_qty = Column(Integer, default=0)
    completed_qty = Column(Integer, default=0)
    good_qty = Column(Integer, default=0)
    ng_qty = Column(Integer, default=0)
    status = Column(String(20), default="pending")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order = relationship("WorkOrder", back_populates="batches")
    workpieces = relationship("Workpiece", back_populates="batch")


# ============================================================
# 工件追溯
# ============================================================

class Workpiece(Base):
    """工件表 — 单件追溯核心表

    状态机: registered -> queued -> inspecting -> ok  (终态)
                                              -> ng -> rework -> inspecting
                                                    -> scrapped (终态)
    """
    __tablename__ = "workpieces"
    __table_args__ = (
        UniqueConstraint("serial_no", "project_id", name="uq_serial_project"),
        Index("ix_workpiece_status", "status"),
        Index("ix_workpiece_order", "order_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    serial_no = Column(String(128), nullable=False, index=True)
    raw_barcode = Column(String(512), nullable=True)

    order_id = Column(Integer, ForeignKey("work_orders.id", ondelete="SET NULL"), nullable=True)
    batch_id = Column(Integer, ForeignKey("batches.id", ondelete="SET NULL"), nullable=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    status = Column(String(20), nullable=False, default="registered")
    inspection_count = Column(Integer, default=0)
    latest_cycle_id = Column(Integer, nullable=True)
    final_result = Column(String(20), nullable=True)

    channel_id = Column(Integer, nullable=True)
    operator = Column(String(64), nullable=True)
    scan_source = Column(String(20), default="manual")
    scan_device_id = Column(Integer, ForeignKey("scanner_devices.id", ondelete="SET NULL"), nullable=True)

    registered_at = Column(DateTime(timezone=True), server_default=func.now())
    first_inspect_at = Column(DateTime(timezone=True), nullable=True)
    last_inspect_at = Column(DateTime(timezone=True), nullable=True)
    extra_data = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    order = relationship("WorkOrder", back_populates="workpieces")
    batch = relationship("Batch", back_populates="workpieces")
    project = relationship("Project", foreign_keys=[project_id])
    inspections = relationship("WorkpieceInspection", back_populates="workpiece",
                               cascade="all, delete-orphan", order_by="WorkpieceInspection.inspection_seq")
    defects = relationship("DefectRecord", back_populates="workpiece", cascade="all, delete-orphan")


class WorkpieceInspection(Base):
    """工件检测关联表 — 记录工件的每一次检测（含返工重检）

    用中间表而非直接在 detection_cycles 加 FK，原因:
    1. 保持现有 Cycle 表结构不变，降低迁移风险
    2. 一个工件可被多次检测 (返工场景)
    3. 中间表可存每次检测的独立结果快照
    """
    __tablename__ = "workpiece_inspections"
    __table_args__ = (
        UniqueConstraint("workpiece_id", "cycle_id", name="uq_workpiece_cycle"),
    )

    id = Column(Integer, primary_key=True, index=True)
    workpiece_id = Column(Integer, ForeignKey("workpieces.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    cycle_id = Column(Integer, nullable=False, index=True)
    session_id = Column(Integer, nullable=True)

    inspection_seq = Column(Integer, nullable=False, default=1)
    result = Column(String(10), nullable=False, default="pending")
    event_name = Column(String(100), nullable=True)
    result_reason = Column(Text, nullable=True)
    channel_id = Column(Integer, nullable=True)
    duration = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    workpiece = relationship("Workpiece", back_populates="inspections")


# ============================================================
# 缺陷管理
# ============================================================

class DefectRecord(Base):
    """缺陷记录表"""
    __tablename__ = "defect_records"

    id = Column(Integer, primary_key=True, index=True)
    defect_uuid = Column(String(32), unique=True, nullable=False, index=True)

    workpiece_id = Column(Integer, ForeignKey("workpieces.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    inspection_id = Column(Integer, ForeignKey("workpiece_inspections.id", ondelete="SET NULL"),
                           nullable=True)
    cycle_id = Column(Integer, nullable=True)
    step_record_id = Column(Integer, nullable=True)

    defect_code = Column(String(32), nullable=False, index=True)
    defect_name = Column(String(128), nullable=False)
    defect_category = Column(String(32), nullable=False, default="other")
    severity = Column(String(16), nullable=False, default="minor")

    confidence = Column(Float, nullable=True)
    detection_label = Column(String(64), nullable=True)
    bbox_x = Column(Float, nullable=True)
    bbox_y = Column(Float, nullable=True)
    bbox_w = Column(Float, nullable=True)
    bbox_h = Column(Float, nullable=True)
    screenshot_path = Column(String(512), nullable=True)
    description = Column(Text, nullable=True)
    source = Column(String(20), default="auto")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    workpiece = relationship("Workpiece", back_populates="defects")
    inspection = relationship("WorkpieceInspection", foreign_keys=[inspection_id])


class DefectCode(Base):
    """缺陷代码字典表 — 定义缺陷类型及自动映射规则

    detection_labels: 检测标签列表, 当 Cycle NG 时
    从 result_reason 匹配标签自动创建缺陷记录
    """
    __tablename__ = "defect_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(32), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    category = Column(String(32), nullable=False, default="other")
    severity = Column(String(16), nullable=False, default="minor")

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    detection_labels = Column(JSON, nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# 扫码器管理
# ============================================================

class ScannerDevice(Base):
    """扫码器设备配置表 (威码视 VS600)"""
    __tablename__ = "scanner_devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), nullable=False)
    ip = Column(String(45), nullable=False)
    port = Column(Integer, nullable=False, default=55256)
    channel_id = Column(Integer, nullable=True)
    enabled = Column(Boolean, default=True)

    parse_mode = Column(String(20), default="direct")
    parse_config = Column(JSON, nullable=True)
    dedup_interval_sec = Column(Integer, default=2)
    auto_create_workpiece = Column(Boolean, default=True)
    auto_link_order = Column(Boolean, default=True)
    scan_required = Column(Boolean, default=False)
    duplicate_scan_action = Column(String(20), default="overwrite")
    warn_no_barcode = Column(Boolean, default=False)
    rebind_mode = Column(String(20), default="rescan")
    bind_timing = Column(String(20), default="mid_cycle")
    broadcast_channels = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ScanLog(Base):
    """扫码记录表"""
    __tablename__ = "scan_logs"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("scanner_devices.id", ondelete="SET NULL"), nullable=True)
    channel_id = Column(Integer, nullable=True)

    raw_data = Column(String(512), nullable=False)
    parsed_serial = Column(String(128), nullable=True)
    parsed_order = Column(String(64), nullable=True)
    parsed_batch = Column(String(64), nullable=True)

    workpiece_id = Column(Integer, ForeignKey("workpieces.id", ondelete="SET NULL"), nullable=True)
    success = Column(Boolean, nullable=False, default=True)
    error_msg = Column(String(256), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ============================================================
# 外部 MES 对接 (第三期表结构预留)
# ============================================================

class MESConnection(Base):
    """外部 MES 连接配置表"""
    __tablename__ = "mes_connections"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), nullable=False)
    adapter_type = Column(String(32), nullable=False, default="rest")
    enabled = Column(Boolean, default=False)

    config = Column(JSON, nullable=True)
    push_events = Column(JSON, nullable=True)
    pull_enabled = Column(Boolean, default=False)
    pull_interval_sec = Column(Integer, default=60)
    retry_count = Column(Integer, default=3)
    retry_interval_sec = Column(Integer, default=5)
    extra_fields_schema = Column(JSON, nullable=True)
    bound_channels = Column(JSON, nullable=True)

    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MESCommLog(Base):
    """MES 通讯日志表"""
    __tablename__ = "mes_comm_logs"

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(Integer, ForeignKey("mes_connections.id", ondelete="SET NULL"), nullable=True)
    direction = Column(String(10), nullable=False)
    event_type = Column(String(32), nullable=True)

    method = Column(String(10), nullable=True)
    url = Column(String(512), nullable=True)
    request_body = Column(Text, nullable=True)
    response_body = Column(Text, nullable=True)
    status_code = Column(Integer, nullable=True)
    success = Column(Boolean, nullable=False, default=True)
    error_msg = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ============================================================
# 集群模式 (多实例主从汇总)
# ============================================================

class ClusterConfig(Base):
    """集群配置表 — 单行配置，id 固定为 1"""
    __tablename__ = "cluster_config"

    id = Column(Integer, primary_key=True, default=1)
    role = Column(String(20), nullable=False, default="standalone")
    master_url = Column(String(256), nullable=True)
    station_id = Column(String(32), nullable=False, default="A")
    expected_stations = Column(JSON, nullable=True)
    sync_mode = Column(String(20), nullable=False, default="wait_all")
    timeout_sec = Column(Integer, nullable=False, default=300)
    enabled = Column(Boolean, default=False)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BoxAggregation(Base):
    """箱子汇总记录 — 按 box_serial 收集各工位数据"""
    __tablename__ = "box_aggregations"
    __table_args__ = (
        Index("ix_box_serial_station", "box_serial", "station_id", unique=True),
        Index("ix_box_status", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    box_serial = Column(String(128), nullable=False, index=True)
    station_id = Column(String(32), nullable=False)
    source_address = Column(String(128), nullable=True)
    channel_id = Column(Integer, nullable=True)

    cycle_context = Column(JSON, nullable=True)
    is_good = Column(Boolean, nullable=True)
    event_name = Column(String(100), nullable=True)

    status = Column(String(20), nullable=False, default="received")
    received_at = Column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# 外部设备接入（称重器、PLC、传感器等）
# ============================================================

class ExternalDevice(Base):
    """外部数据设备配置表 — 通用接入各类工业设备

    支持协议: tcp, modbus_tcp, serial, http_poll
    数据角色: weight(称重), sensor(传感器), plc(PLC信号), custom(自定义)
    """
    __tablename__ = "external_devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), nullable=False)
    device_role = Column(String(20), nullable=False, default="weight")
    protocol = Column(String(20), nullable=False, default="tcp")

    ip = Column(String(45), nullable=True)
    port = Column(Integer, nullable=True)
    serial_port = Column(String(32), nullable=True)
    serial_baud = Column(Integer, nullable=True, default=9600)

    # 协议配置（JSON，各协议不同字段）
    # tcp: {"delimiter": "\r\n"}
    # modbus_tcp: {"unit_id": 1, "register": 0, "count": 2, "func": "holding", "poll_interval": 1.0}
    # serial: {"delimiter": "\r\n", "bytesize": 8, "parity": "N", "stopbits": 1}
    # http_poll: {"url": "http://...", "method": "GET", "interval": 2.0, "json_path": "data.weight"}
    protocol_config = Column(JSON, nullable=True)

    # 数据解析
    # parse_mode: direct(整行就是值), regex(正则提取), json_path(JSON字段), split(分隔符)
    parse_mode = Column(String(20), nullable=False, default="direct")
    # parse_config 示例:
    # regex: {"pattern": "([\\d.]+)\\s*kg", "group": 1, "fields": {"weight": 1}}
    # split: {"delimiter": ",", "fields": {"barcode": 0, "weight": 1}}
    # json_path: {"fields": {"weight": "data.weight", "unit": "data.unit"}}
    parse_config = Column(JSON, nullable=True)

    # 业务配置
    station_id = Column(String(32), nullable=True)
    channel_id = Column(Integer, nullable=True)
    # 数据流向: cluster(注入ClusterCollector), extra_fields(注入MES extra), both(两者)
    data_target = Column(String(20), nullable=False, default="cluster")
    # 范围校验（如称重范围）
    # {"weight": {"min": 10.0, "max": 50.0}}
    validation_rules = Column(JSON, nullable=True)
    enabled = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ExternalDeviceLog(Base):
    """外部设备数据日志"""
    __tablename__ = "external_device_logs"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("external_devices.id", ondelete="SET NULL"), nullable=True)
    raw_data = Column(String(1024), nullable=True)
    parsed_data = Column(JSON, nullable=True)
    box_serial = Column(String(128), nullable=True)
    is_valid = Column(Boolean, default=True)
    error_msg = Column(String(256), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class BoxSummary(Base):
    """箱子汇总结果 — 所有工位到齐后生成"""
    __tablename__ = "box_summaries"

    id = Column(Integer, primary_key=True, index=True)
    box_serial = Column(String(128), nullable=False, unique=True, index=True)
    total_stations = Column(Integer, nullable=False, default=0)
    completed_stations = Column(Integer, nullable=False, default=0)
    overall_result = Column(String(10), nullable=True)
    aggregated_context = Column(JSON, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    pushed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
