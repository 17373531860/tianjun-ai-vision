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

    # v3.1.0: 工单绑定范围 — project / channels / cluster (三选一)
    #   project  : 按 project_id 匹配, 任意工位都可消费 (老逻辑)
    #   channels : 按本机 channel_id 白名单匹配 (target_channels), 不看 project_id
    #   cluster  : 按集群 station_id 白名单匹配 (target_stations), 一个 box_serial 算一件
    #              cluster 模式 +1 不在 _handle_cycle_end 触发, 由 cluster_collector
    #              在 box_complete 时调 increment_completed.
    binding_scope = Column(String(20), nullable=False, default="project", index=True)
    target_channels = Column(JSON, nullable=True)   # [0, 1] 工位号列表
    target_stations = Column(JSON, nullable=True)   # ["A", "B"] 集群站点列表

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

    cycle_id / session_id 加 FK ondelete="SET NULL"：
    cycle 被删时只置空，保留检测痕迹，避免孤儿记录污染统计。
    旧库不强制加 FK 约束（SQLite ALTER TABLE 限制），仅新建库生效。
    """
    __tablename__ = "workpiece_inspections"
    __table_args__ = (
        UniqueConstraint("workpiece_id", "cycle_id", name="uq_workpiece_cycle"),
    )

    id = Column(Integer, primary_key=True, index=True)
    workpiece_id = Column(Integer, ForeignKey("workpieces.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    cycle_id = Column(Integer, ForeignKey("detection_cycles.id", ondelete="SET NULL"),
                      nullable=True, index=True)
    session_id = Column(Integer, ForeignKey("detection_sessions.id", ondelete="SET NULL"),
                        nullable=True)

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
    cycle_id = Column(Integer, ForeignKey("detection_cycles.id", ondelete="SET NULL"),
                      nullable=True)
    step_record_id = Column(Integer, ForeignKey("step_records.id", ondelete="SET NULL"),
                            nullable=True)

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
    device_type = Column(String(20), default="text_lon")
    # 勾上后，扫码只用来把条码喂给"绑定工位"里挂着的外部设备（如称重器），
    # 不会触发任何视觉检测周期。用于"扫码-放秤"这类只服务外设的扫码枪。
    external_only = Column(Boolean, default=False)
    # v2.8.1 设备分组号：扫码枪和外设（如秤）之间的配对键，与"绑定工位"解耦。
    # 为空时沿用旧逻辑——用 channel_id 做配对。
    pairing_group = Column(String(32), nullable=True)
    # 同码二次扫抑制：若同条码对应工件上次检测合格、且距完成时间不超过该秒数，本次扫码静默丢弃。
    # 用于过滤"搬运过程中扫码器误扫到已完成合格工件"的场景。0 = 关闭。
    ok_rescan_cooldown_sec = Column(Integer, default=0)
    # v2.7.16 迟到扫码补绑窗口：cycle 已结算但 _inspecting_workpiece 为空时，
    # 若 _pending_workpiece 中存在的扫码事件与本次 cycle 结束时间相差 <= 该秒数，
    # 自动把这枚工件补绑到刚结算的 cycle，避免"扫码晚一步导致未绑码"。
    # 0 = 关闭（保持旧行为）。
    late_scan_bind_window_sec = Column(Integer, default=3)
    # v2.7.16 扫描模式 (text_lon 协议下的"灯/扫描节奏"控制):
    #   continuous     = 当前默认: ERROR/扫到码后立刻续 LON, 灯持续闪等下一码
    #   throttled      = 同 continuous, 但 ERROR/扫到码后等 throttle_idle_ms 才续 LON,
    #                    降低闪烁频率, 减少蜂鸣 / 省电
    #   once_per_cycle = 扫到码后发 LOFF 灯灭, 等当前周期结束时由后端自动重新发 LON
    #                    一个工件扫一次, 适合"先扫后检"的强校验工位
    scan_mode = Column(String(32), default="continuous")
    # 仅 scan_mode="throttled" 时生效, ERROR/扫到码后等待多少毫秒再续 LON.
    throttle_idle_ms = Column(Integer, default=500)

    # v3.1.2 多工位广播结算联动 (仅当 broadcast_channels 非空时生效):
    #   independent = 各工位独立结算 (默认, 旧行为)
    #   primary     = 由 primary_settle_channel 指定的"主工位"结算时,
    #                 通知其他广播工位强制结算当前周期, 实现大小件同步.
    broadcast_settle_mode = Column(String(20), default="independent")
    # primary 模式下, 哪个 channel 作为节拍源 (必须出现在 broadcast_channels 里).
    primary_settle_channel = Column(Integer, nullable=True)
    # primary 模式下跟随结算的最低件数门槛: 箱子 / 周期里已检出物品数 < 该值时跳过,
    # 避免上游空箱子被冤判 NG. 默认 1 (放了一件就跟随), 0 = 不过滤, N = 至少 N 件.
    primary_settle_min_items = Column(Integer, default=1)

    # v3.3.0 码-码闭环结算 (bind_timing="scan_pair"): 扫码 A 开周期, 扫码 B (≠A)
    # 触发结算 A 周期 + 启动 B 周期。判定 OK/NG 依据"窗口内物品/箱子是否曾齐过",
    # 不依赖物理消失或 ROI 退出。下面字段仅 bind_timing="scan_pair" 时生效:
    # 扫码 A 后等待 B 的最大秒数: 0 = 不超时 (推荐工人节拍稳定时),
    # > 0 = 到时强制 NG 结算, 防止漏扫导致周期永远卡死.
    scan_pair_max_wait_sec = Column(Integer, default=0)

    # v3.4.0 D 容器跨线/区域触发扫码 (scan_mode='D' 时生效, 仅容器模式项目可启用):
    # 几何选择:
    #   "line"  = 一条线段 + 方向, 容器跨线触发 LON, 扫到码自动 LOFF
    #   "zone"  = 一个 polygon 区域, 容器中心点进入触发 LON, 扫到码自动 LOFF
    scan_d_geometry = Column(String(8), default="line")
    # 线模式几何配置 (像素坐标, 相对原始视频帧):
    #   {x1, y1, x2, y2, side_a_to_b: bool}
    # side_a_to_b=True: 从线左侧(A)→右侧(B)触发; False: 从右(B)→左(A)触发
    scan_d_line = Column(JSON, nullable=True)
    # 区域模式几何配置 (像素坐标 polygon): [[x,y], ...] 至少 3 个点
    scan_d_zone = Column(JSON, nullable=True)
    # D 模式专属"box 离开后多少帧才视为可触发下一个" 默认 30 帧 (= 1秒@30fps).
    # 0 表示沿用项目 pipeline_config.gone_confirm_frames.
    scan_d_gone_confirm_frames = Column(Integer, default=30)

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
    timeout_push = Column(Boolean, default=False)
    enabled = Column(Boolean, default=False)
    # 本机"视觉通道 → 站点"映射表，例如 {"0":"B","1":"B"} 表示两路视觉都上报到站点 B。
    # 为空时沿用旧逻辑：channel_count>1 自动拼 "{station_id}-{channel_id}"。
    channel_station_map = Column(JSON, nullable=True)
    # v3.1.2 站点结果合并策略 (同一 station_id 多次上报时):
    #   "latest"   = 默认 / 现状: 同一路最新覆盖, 多路 AND 判 OK/NG (任一路 NG → 整站 NG)
    #   "ok_lock"  = OK 锁定: 已合格的站点不被 NG 覆盖, NG 站点可被 OK 翻盘.
    #                被拒的 NG sub_report 仍写入审计, 不影响 is_good.
    station_result_strategy = Column(String(20), default="latest")

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
    # v2.8.1 设备分组号：和扫码枪的配对键，与"绑定工位"解耦。
    # 为空时沿用旧逻辑——用 channel_id 做配对。
    pairing_group = Column(String(32), nullable=True)
    # 数据流向: cluster(注入ClusterCollector), extra_fields(注入MES extra), both(两者)
    data_target = Column(String(20), nullable=False, default="cluster")
    # 范围校验（如称重范围）
    # {"weight": {"min": 10.0, "max": 50.0}}
    validation_rules = Column(JSON, nullable=True)
    enabled = Column(Boolean, default=True)

    # v2.7.5: 稳定值判定（称重抖动过滤）
    # 连续 stable_count 次读数之间最大差 <= stable_delta (同单位) 才视为稳定；
    # 空载判断：|value| < zero_threshold 时视为无物品，会清空对应条码 buffer。
    # stable_enabled=False 时禁用该判定，保持旧行为（每次数据直接上报）。
    stable_enabled = Column(Boolean, default=True)
    stable_delta = Column(Float, default=0.05)
    stable_count = Column(Integer, default=5)
    zero_threshold = Column(Float, default=0.05)

    # v2.7.5: 有重无码告警
    # 开启后，当 |value| >= zero_threshold 但 barcode buffer 为空持续
    # weight_no_barcode_alarm_delay_sec 秒时，触发 "weight_no_barcode" 事件。
    weight_no_barcode_alarm_enabled = Column(Boolean, default=False)
    weight_no_barcode_alarm_delay_sec = Column(Integer, default=10)

    # v3.1.1: 配对模式 — stable / instant
    # stable  (默认, 老逻辑): 等称重稳定 → 用稳定值绑定扫码; 流水线"工件不回零"场景
    #         会出现"新条码绑到旧重量"的错位.
    # instant (新): 扫码瞬间立即用最近一次称重读数绑定 + 派发后立即清 buffer.
    #         适合"工件来不及回零、不允许丢数据"的连续上料流水线;
    #         代价是个别瞬时读数可能不准 (如 A 未离开 B 已经压上, 读数=A+B).
    pairing_mode = Column(String(16), default="stable")

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
