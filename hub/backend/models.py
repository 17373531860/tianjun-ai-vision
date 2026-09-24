"""Fleet Hub ORM 模型 (RFC 15 §8 数据模型草案)。

M1 落地: hub_users / hub_tokens / hub_nodes / hub_stations / hub_twins / hub_audit_logs
M3 落地: hub_station_locks;  P1 再加: hub_scene_rules / hub_ota_*
"""
from datetime import datetime

from sqlalchemy import (JSON, Boolean, Column, DateTime, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import relationship

from hub.backend.db import Base


class HubUser(Base):
    """枢纽用户。role 三档: admin / director / engineer / operator (权限映射见 auth.ROLE_PERMS)"""
    __tablename__ = "hub_users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    display_name = Column(String(128))
    role = Column(String(32), nullable=False, default="operator")
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class HubToken(Base):
    """登录 token (sha256 落库; 明文只在 login 响应出现一次)"""
    __tablename__ = "hub_tokens"

    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("hub_users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.now)

    user = relationship("HubUser")


class HubNode(Base):
    """纳管的边缘机 + 最近能力档案快照"""
    __tablename__ = "hub_nodes"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    base_url = Column(String(256), unique=True, nullable=False)   # http://192.168.1.10:8001
    api_key_enc = Column(Text)          # Fernet 加密的边缘 M2M API Key (scope=hub)
    node_uid = Column(String(64))       # 边缘 identity.node_id (edge-xxxx)
    machine_id = Column(String(128))
    hostname = Column(String(128))
    app_version = Column(String(32))
    api_contract = Column(Integer, default=1)
    license_state = Column(String(16), default="unknown")  # valid / expired / unknown
    profile = Column(JSON)              # 最近完整能力档案
    profile_hash = Column(String(80))
    enabled = Column(Boolean, default=True)
    # P0-10 边缘事件游标: NULL=从未拉过 (首拉"从现在订阅"), >=0=已订阅增量位.
    # 不能用 0 兼任"从未拉过"—— 空边缘首拉后游标就是 0, 会永远走首拉分支
    # 把第一批真实事件当历史帐跳过 (测试期踩过的真 bug)。
    event_cursor = Column(Integer, nullable=True, default=None)
    # M5 数据中心周期游标: 与 event_cursor 语义相同但独立 —— 报警通道只拉 NG
    # (4s 快节奏), 统计通道拉全量 OK+NG (慢节奏大页)。两游标互不干扰,
    # 任一通道故障不拖累另一条。
    cycle_cursor = Column(Integer, nullable=True, default=None)
    created_at = Column(DateTime, default=datetime.now)

    stations = relationship("HubStation", cascade="all, delete-orphan",
                            back_populates="node")


class HubStation(Base):
    """工位映射 (从档案自动发现, 可改名/分组)"""
    __tablename__ = "hub_stations"

    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, ForeignKey("hub_nodes.id"), nullable=False, index=True)
    channel_id = Column(Integer, nullable=False)
    display_name = Column(String(128))
    group_name = Column(String(128))    # 产线分组 (资源树 P0 的最小形态)

    node = relationship("HubNode", back_populates="stations")


class HubTwin(Base):
    """设备孪生 (RFC 15 §4.2)。M1 只写 reported 侧; desired 留 M3 写操作接入。"""
    __tablename__ = "hub_twins"

    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, ForeignKey("hub_nodes.id"), nullable=False, index=True)
    channel_id = Column(Integer, nullable=False)
    desired = Column(JSON, default=dict)
    reported = Column(JSON, default=dict)
    version = Column(Integer, default=0)      # reported 变更时 ++
    reported_at = Column(DateTime)


class HubStationLock(Base):
    """操作锁 (RFC 15 §10.1)。一工位一行; 过期锁视同无锁, 由 lock_manager 收割。

    落 DB 而非内存: 枢纽重启后锁状态不丢 (否则重启瞬间人人可写, 违背互斥承诺)。
    """
    __tablename__ = "hub_station_locks"
    __table_args__ = (UniqueConstraint("node_id", "channel_id",
                                       name="uq_lock_station"),)

    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, ForeignKey("hub_nodes.id"), nullable=False, index=True)
    channel_id = Column(Integer, nullable=False)
    username = Column(String(64), nullable=False)
    acquired_at = Column(DateTime, default=datetime.now)
    expires_at = Column(DateTime, nullable=False)


class HubEvent(Base):
    """边缘事件落库 (P0-10 事件通道, 报警中心数据源)。

    只存 NG 类 (poller 拉取时按 result=ng 过滤): OK 周期量大且报警中心不消费。
    (node_id, edge_event_id) 唯一 —— 游标重放/枢纽重启不产生重复行。
    保留策略: 插入时超 EVENT_KEEP_MAX 删最老 (poller 收口)。
    """
    __tablename__ = "hub_events"
    __table_args__ = (UniqueConstraint("node_id", "edge_event_id",
                                       name="uq_event_edge"),)

    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, ForeignKey("hub_nodes.id"), nullable=False, index=True)
    channel_id = Column(Integer, nullable=False, default=0)
    edge_event_id = Column(Integer, nullable=False)   # 边缘 DetectionCycle.id
    kind = Column(String(32), default="cycle")
    result = Column(String(8))                        # OK / NG
    event_name = Column(String(128))
    reason = Column(Text)
    ts = Column(String(40))                           # 边缘侧结算时刻 (ISO 串)
    acked_by = Column(String(64))                     # 报警中心处理人 (空=未处理)
    acked_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now, index=True)


class HubCycle(Base):
    """周期明细 (M5 数据中心, 证据层)。

    统计通道拉取的全量结算周期 (OK+NG), 与 hub_events (报警中心, 只 NG) 独立。
    (node_id, edge_cycle_id) 唯一 —— 游标重放/枢纽重启不产生重复行。
    保留 CYCLE_KEEP_DAYS 天 (默认 90), retention 线程收口; 更久的历史只留小时桶。
    """
    __tablename__ = "hub_cycles"
    __table_args__ = (
        UniqueConstraint("node_id", "edge_cycle_id", name="uq_cycle_edge"),
        # 明细扫描全部按小时桶取, 单列桶索引足够 (数据量 ~2 万行/天)
        Index("ix_cycles_bucket", "bucket_epoch"),
    )

    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, ForeignKey("hub_nodes.id"), nullable=False, index=True)
    channel_id = Column(Integer, nullable=False, default=0)
    edge_cycle_id = Column(Integer, nullable=False)   # 边缘 DetectionCycle.id
    result = Column(String(8))                        # OK / NG
    event_name = Column(String(128))
    reason = Column(Text)                             # NG 原因 (边缘结算原样)
    project_id = Column(Integer, default=0)           # 边缘侧项目 id (0=无项目)
    project_name = Column(String(128))
    duration_ms = Column(Integer)                     # 周期耗时毫秒 (None=边缘无耗时)
    ts_epoch = Column(Integer, nullable=False)        # 边缘结算时刻 (epoch 秒)
    bucket_epoch = Column(Integer, nullable=False)    # 所属小时桶 = ts//3600*3600
    created_at = Column(DateTime, default=datetime.now)


class HubCycleHourly(Base):
    """小时桶产量聚合 (M5)。粒度: 节点 × 工位 × 项目 × 小时。

    由 rollup worker 从 hub_cycles GROUP BY 重算 (脏桶机制, 幂等覆盖写),
    /stats 查询全部走这张表 —— 合格率必须在查询层 sum(ok)/sum(total),
    禁止对本表行的比率做平均 (加权口径, 调研裁决)。
    保留 ROLLUP_KEEP_DAYS 天 (默认 730)。
    """
    __tablename__ = "hub_cycle_hourly"
    __table_args__ = (
        UniqueConstraint("node_id", "channel_id", "project_id", "bucket_epoch",
                         name="uq_hourly"),
        Index("ix_hourly_bucket", "bucket_epoch"),
    )

    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, nullable=False, index=True)
    channel_id = Column(Integer, nullable=False, default=0)
    # 0=无项目; 不用 NULL —— SQLite UNIQUE 视每个 NULL 互不相等, 会重复建行
    project_id = Column(Integer, nullable=False, default=0)
    project_name = Column(String(128))                # 快照 (重算时取该桶最新)
    bucket_epoch = Column(Integer, nullable=False)
    count_total = Column(Integer, nullable=False, default=0)
    count_ok = Column(Integer, nullable=False, default=0)
    count_ng = Column(Integer, nullable=False, default=0)
    sum_duration_ms = Column(Integer, nullable=False, default=0)
    cnt_duration = Column(Integer, nullable=False, default=0)  # 有耗时的行数 (均值分母)
    updated_at = Column(DateTime, default=datetime.now)


class HubNgHourly(Base):
    """小时桶 NG 原因聚合 (M5, Pareto/原因×工位交叉表数据源)。

    reason 规整规则见 rollup._norm_reason (空→"未知原因", 截断 120 字符)。
    """
    __tablename__ = "hub_ng_hourly"
    __table_args__ = (
        UniqueConstraint("node_id", "channel_id", "reason", "bucket_epoch",
                         name="uq_ng_hourly"),
        Index("ix_ng_hourly_bucket", "bucket_epoch"),
    )

    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, nullable=False, index=True)
    channel_id = Column(Integer, nullable=False, default=0)
    reason = Column(String(128), nullable=False)
    bucket_epoch = Column(Integer, nullable=False)
    count_ng = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.now)


class HubRollupDirty(Base):
    """脏桶登记 (M5)。poller 写明细时标脏 (node × 小时), rollup worker 消费。

    粒度到 node 而非 channel: 重算按"该节点该小时"整桶 GROUP BY 覆盖写,
    单桶行数 ≤ 数百, 重算成本可忽略, 换来迟到数据/游标重放天然幂等。
    """
    __tablename__ = "hub_rollup_dirty"
    __table_args__ = (UniqueConstraint("node_id", "bucket_epoch",
                                       name="uq_dirty"),)

    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, nullable=False)
    bucket_epoch = Column(Integer, nullable=False)
    marked_at = Column(DateTime, default=datetime.now)


class HubNodeStatusEvent(Base):
    """节点上下线切换史 (M7 运维告警链路)。

    每次 online↔offline 切换一行; online 行的 duration_s = 结束的那段
    离线时长 (首见/枢纽重启后的初始转换 duration 为 NULL 不计账)。
    notified: 该行是否已走通知出口 (离线超阈值告警 / 恢复通知), 幂等标记。
    """
    __tablename__ = "hub_node_status_events"

    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, ForeignKey("hub_nodes.id"), nullable=False,
                     index=True)
    status = Column(String(16), nullable=False)      # online / offline
    ts = Column(DateTime, nullable=False, default=datetime.now, index=True)
    duration_s = Column(Integer, nullable=True)      # online 行: 上一段离线秒数
    error = Column(Text, nullable=True)              # offline 行: 判离线时错误
    notified = Column(Boolean, nullable=False, default=False)


class HubSetting(Base):
    """枢纽通用 KV 配置 (M7 首用于通知出口 notify; JSON 值)"""
    __tablename__ = "hub_settings"

    key = Column(String(64), primary_key=True)
    value = Column(JSON, nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class HubAuditLog(Base):
    """审计 — 只增不改 (RFC 15 §4.3)"""
    __tablename__ = "hub_audit_logs"

    id = Column(Integer, primary_key=True)
    username = Column(String(64))
    node_id = Column(Integer, index=True)
    channel_id = Column(Integer)
    action = Column(String(64), nullable=False, index=True)
    old_value = Column(Text)
    new_value = Column(Text)
    source_ip = Column(String(64))
    result = Column(String(16), default="ok")   # ok / denied / failed
    created_at = Column(DateTime, default=datetime.now, index=True)
