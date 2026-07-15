from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, Float, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.db.database import Base

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), index=True, nullable=False)
    task_type = Column(String(50), default="detection")  # detection, segmentation, etc.
    pipeline_config = Column(JSON, nullable=True)
    default_model_id = Column(Integer, ForeignKey("models.id", ondelete="SET NULL"), nullable=True)
    logic_mode = Column(String(50), default="sequential")  # sequential / detection / custom / tracking / per_item
    steps_config = Column(JSON, nullable=True)  # 步骤配置
    events_config = Column(JSON, nullable=True)  # 事件配置
    counters_config = Column(JSON, nullable=True)  # 计数器配置
    alarm_config = Column(JSON, nullable=True)  # 报警设置
    detection_config = Column(JSON, nullable=True)  # 检测框设置
    data_config = Column(JSON, nullable=True)  # 数据导出设置
    model_format = Column(String(50), default="pytorch_fp32")  # 该项目使用的推理格式
    is_active = Column(Boolean, default=False)  # 是否激活
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    default_model = relationship("Model", foreign_keys=[default_model_id], post_update=True)
    project_models = relationship("Model", back_populates="project", foreign_keys="Model.project_id")
    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan")
    detection_sessions = relationship("DetectionSession", back_populates="project", cascade="all, delete-orphan")

class Model(Base):
    __tablename__ = "models"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(200), index=True, nullable=False)
    file_path = Column(String(500), nullable=False)
    file_name = Column(String(200), nullable=False)
    file_size = Column(Integer, default=0)  # bytes
    framework = Column(String(50), default="PyTorch")  # PyTorch, ONNX, TensorRT
    labels = Column(JSON, nullable=True)  # 模型标签列表
    description = Column(Text, nullable=True)
    version = Column(String(50), nullable=True)
    status = Column(String(20), default="idle")  # idle, active
    upload_time = Column(DateTime(timezone=True), server_default=func.now())

    project = relationship("Project", back_populates="project_models", foreign_keys=[project_id])
    tasks = relationship("Task", back_populates="model")
    conversions = relationship("ModelConversion", back_populates="model", cascade="all, delete-orphan")


class ModelConversion(Base):
    """模型格式转换记录 — 按 model_id+format+gpu_arch 唯一索引，跨项目共享"""
    __tablename__ = "model_conversions"
    __table_args__ = (
        UniqueConstraint("model_id", "format", "gpu_arch", name="uq_model_format_gpu"),
    )

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(Integer, ForeignKey("models.id", ondelete="CASCADE"), nullable=False)
    format = Column(String(50), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, default=0)
    gpu_name = Column(String(200), nullable=True)
    gpu_arch = Column(String(50), nullable=True)
    status = Column(String(20), default="queued")  # queued, converting, ready, failed
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    model = relationship("Model", back_populates="conversions")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))
    model_id = Column(Integer, ForeignKey("models.id", ondelete="SET NULL"), nullable=True)
    input_file = Column(String(500), nullable=True)  # Path to input image/video
    result_file = Column(String(500), nullable=True)  # Path to result
    result_data = Column(JSON, nullable=True)  # 检测结果数据
    is_good = Column(Boolean, default=True)
    confidence = Column(Float, nullable=True)
    duration = Column(Integer, default=0)  # In milliseconds
    step_name = Column(String(100), nullable=True)  # 步骤名称
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    error_msg = Column(Text, nullable=True)

    project = relationship("Project", back_populates="tasks")
    model = relationship("Model", back_populates="tasks")

class Camera(Base):
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    source = Column(String(500), nullable=False)  # 相机索引或URL
    camera_type = Column(String(50), default="usb")  # usb, rtsp, hikvision, daheng
    resolution_width = Column(Integer, default=1280)
    resolution_height = Column(Integer, default=720)
    fps = Column(Integer, default=30)
    exposure = Column(Float, nullable=True)
    is_active = Column(Boolean, default=False)
    status = Column(String(20), default="offline")  # online, offline
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class DailyStat(Base):
    """每日统计汇总表"""
    __tablename__ = "daily_stats"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String(10), index=True, nullable=False)  # YYYY-MM-DD
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    good_count = Column(Integer, default=0)
    bad_count = Column(Integer, default=0)
    total_count = Column(Integer, default=0)
    total_duration = Column(Integer, default=0)  # milliseconds
    yield_rate = Column(Float, default=0.0)  # 良率

class SystemConfig(Base):
    """系统配置表"""
    __tablename__ = "system_configs"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    description = Column(String(500), nullable=True)


# v3.10+ 阶段 5: Operator 类已删除, 改由 backend/models/auth_models.py:User 接管.
# detection_sessions.operator_id / detection_cycles.operator_id 列保留 (SQLite 不支持 rename),
# FK 改指向 users.id, 语义重定向到当前登录用户 id.
# 老 schema 里 FK 指向 operators.id 不会引发问题 — SQLite 默认 PRAGMA foreign_keys=OFF.


class DetectionSession(Base):
    """检测会话表 - 记录设备启动到关闭的一个完整时间段"""
    __tablename__ = "detection_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_uuid = Column(String(50), unique=True, index=True, nullable=False)  # 系统唯一标识 (8 位 hex)
    name = Column(String(64), nullable=True, index=True)  # 客户自定义会话标识 (可选, 文件名/筛选用)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))
    
    start_time = Column(DateTime(timezone=True), nullable=False)  # 启动时间
    end_time = Column(DateTime(timezone=True), nullable=True)  # 结束时间
    
    # 汇总统计
    total_cycles = Column(Integer, default=0)  # 总周期数
    good_cycles = Column(Integer, default=0)  # 合格周期数
    ng_cycles = Column(Integer, default=0)  # 不良周期数
    counters_snapshot = Column(JSON, nullable=True)  # 计数器快照 {counter_name: value}
    
    # 时间统计
    avg_cycle_time = Column(Float, default=0)  # 平均周期时间（秒）
    min_cycle_time = Column(Float, nullable=True)  # 最短周期时间
    max_cycle_time = Column(Float, nullable=True)  # 最长周期时间
    
    # 视频
    video_path = Column(String(500), nullable=True)  # 整个会话的视频路径
    video_id = Column(String(50), nullable=True)  # 视频ID，用于快速检索
    
    # 状态
    status = Column(String(20), default="running")  # running, completed, interrupted
    
    # Multi-channel (workstation) support
    channel_id = Column(Integer, default=0, index=True)  # 0-based workstation index
    
    # 班次标记 ("day" / "night" / null if shift splitting disabled)
    shift_label = Column(String(20), nullable=True, index=True)
    
    # v3.10+ 阶段 5: 字段名保留, 语义重定向 — 写入"当前登录 user.id"; FK 改指 users
    operator_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # MES 工单关联（迁移 v2.7.x 新增）
    order_id = Column(Integer, nullable=True, index=True)

    # 关系 (Operator → User 切换不再注册 relationship, 用代码层 join: backend/api/sessions.py)
    project = relationship("Project", back_populates="detection_sessions")
    cycles = relationship("DetectionCycle", back_populates="session", cascade="all, delete-orphan")


class DetectionCycle(Base):
    """检测周期表 - 记录一轮完整的检测过程"""
    __tablename__ = "detection_cycles"

    id = Column(Integer, primary_key=True, index=True)
    cycle_uuid = Column(String(50), unique=True, index=True, nullable=False)  # 唯一标识
    session_id = Column(Integer, ForeignKey("detection_sessions.id", ondelete="CASCADE"))
    cycle_number = Column(Integer, default=1)  # 周期序号（在会话内）
    
    start_time = Column(DateTime(timezone=True), nullable=False)  # 开始时间
    end_time = Column(DateTime(timezone=True), nullable=True)  # 结束时间
    duration = Column(Float, nullable=True)  # 持续时间（秒）
    interval_to_next = Column(Float, nullable=True)  # 到下一周期的间隔时间（秒）
    
    # 结果
    is_good = Column(Boolean, default=True)  # 是否合格
    event_id = Column(Integer, nullable=True)  # 触发的事件ID
    event_name = Column(String(100), nullable=True)  # 事件名称
    result_reason = Column(Text, nullable=True)  # 结果原因
    
    # 步骤序列
    step_sequence = Column(JSON, nullable=True)  # 步骤顺序 ['步骤1', '步骤2', ...]
    
    # 视频
    video_path = Column(String(500), nullable=True)  # 周期视频路径
    video_id = Column(String(50), nullable=True)  # 视频ID
    
    # v3.10+ 阶段 5: 字段名保留, 语义重定向到当前登录 user.id; FK 改指 users
    operator_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # MES 工单关联（迁移 v2.7.x 新增）
    order_id = Column(Integer, nullable=True, index=True)

    # v3.7.2 外部桥接元数据 (JSON dict).
    # 用途: 在 cycle_start 时锁定外部数据快照, cycle_end 渲染时直接用,
    # 避免"翻文件夹取最新"的所有时序坑.
    # 现行结构:
    #   {
    #     "scan_snapshots": {
    #       "<规范化的 input_dir>": {
    #         "filename": "WP001.txt",
    #         "text": "WP20260513_001",
    #         "mtime": 1747109700.0,
    #         "snapshot_at": "2026-05-13T03:15:00+08:00"
    #       }
    #     }
    #   }
    # 未来可塞: 外部温度/压力/批号等 cycle 开始那一刻的环境数据.
    external_meta = Column(JSON, nullable=True)

    # v3.13 RFC 10: 工位组联动 (单机内多通道结算联动). 独立通道为 NULL.
    channel_group_id = Column(Integer, ForeignKey("channel_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    # 同组联动结算时涉及的伙伴 cycle_id 列表 (审计 / 回溯用), 例: [42, 43]
    group_settled_with = Column(JSON, nullable=True)
    # 组级最终结果 (OK/NG/NULL), 与 is_good 同时存在但语义不同:
    #   - is_good: 本通道独立结算的原始结果 (永不被组级联动覆盖, 报表用)
    #   - group_settle_result: 组级联动后的最终结果 (实际业务采用, 例 MES 推送)
    group_settle_result = Column(String(8), nullable=True)

    # 关系 (Operator → User 切换不再注册 relationship, 用代码层 join: backend/services/mes_gateway.py 等)
    session = relationship("DetectionSession", back_populates="cycles")
    step_records = relationship("StepRecord", back_populates="cycle", cascade="all, delete-orphan")


class ChannelGroup(Base):
    """单机内多通道结算联动组 (v3.13+, RFC 10).

    与 cluster_config 的差异:
      - cluster_config 是 "跨机聚齐 box", 每台机一行配置
      - channel_groups 是 "同机内 channel 联动结算", 一台机可以有 0~N 个组

    零差异默认: 表为空时所有通道独立结算, 行为与 v3.12 完全一致.
    """
    __tablename__ = "channel_groups"

    id = Column(Integer, primary_key=True, index=True)
    # 例 "Group-A" / "front-and-back-cameras"
    name = Column(String(64), nullable=False, unique=True)
    # 成员通道 id 列表, 例 [0, 1] 或 [0, 1, 2, 3]
    member_channel_ids = Column(JSON, nullable=False)
    # settle_strategy 枚举:
    #   synchronized_any_ng (默认 — 客户需求 4): 任一通道 NG → 立即广播给其它成员
    #   synchronized_all_ok: 所有成员都 OK 才整组 OK
    #   independent (等价无组): 不做联动 (主要用于灰度 / 测试)
    #   master_slave: 主通道结算决定整组 (v3.13 暂不实现, 留枚举位)
    settle_strategy = Column(String(32), nullable=False, default="synchronized_any_ng")
    # 组内成员结算等待对方的最长时间, 超时按 timeout_action 处理
    timeout_ms = Column(Integer, nullable=False, default=5000)
    # 超时行为:
    #   fallback_independent (默认): 放弃联动, 各 cycle 按原结算保留
    #   force_ng: 超时的成员强制判 NG
    timeout_action = Column(String(32), nullable=False, default="fallback_independent")
    enabled = Column(Boolean, default=True)
    # v3.13 M3 风格: 客户级附加配置挂这里, 不污染主 schema
    plugin_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StepRecord(Base):
    """步骤记录表 - 记录每次检测到的步骤详情"""
    __tablename__ = "step_records"

    id = Column(Integer, primary_key=True, index=True)
    record_uuid = Column(String(50), unique=True, index=True, nullable=False)  # 唯一标识
    # v3.38 index=True: 周期收尾/自动清理/数据页都按周期号查步骤, 老库由
    # m0001_hot_path_indexes 迁移补建 (索引名 ix_step_records_cycle_id 两边一致)
    cycle_id = Column(Integer, ForeignKey("detection_cycles.id", ondelete="CASCADE"), index=True)

    step_id = Column(String(50), nullable=True)  # 步骤ID（来自项目配置）
    step_label = Column(String(100), nullable=False)  # 步骤标签（检测类别名）
    step_name = Column(String(100), nullable=True)  # 步骤显示名称
    step_order = Column(Integer, default=0)  # 在周期内的顺序
    
    # 时间信息
    start_time = Column(DateTime(timezone=True), nullable=False)  # 开始检测时间
    end_time = Column(DateTime(timezone=True), nullable=True)  # 结束检测时间
    duration = Column(Float, nullable=True)  # 持续时间（秒）
    interval_from_prev = Column(Float, nullable=True)  # 与上一步骤的间隔时间（秒）- 旧字段保留兼容
    interval_to_next = Column(Float, nullable=True)  # 到下一步骤的间隔时间（秒）
    
    # 检测信息
    confidence = Column(Float, nullable=True)  # 置信度
    is_valid = Column(Boolean, default=True)  # 是否有效（满足时间要求）
    
    # 截图和视频
    screenshot_path = Column(String(500), nullable=True)  # 截图路径
    video_path = Column(String(500), nullable=True)  # 步骤视频路径
    video_id = Column(String(50), nullable=True)  # 视频ID

    # v3.13 M3.3: 插件命名空间字段 (PluginHost.write_plugin_step_field 写入)
    # 结构: {"plugin_<customer_code>_<key>": <value>, ...}
    # 主程序导出 / CSV / 默认序列化均不暴露此字段, 仅自定义导出模板可显式取
    # ({step.plugin_data.<plugin_namespaced_key>}).
    plugin_data = Column(JSON, nullable=True)

    # 关系
    cycle = relationship("DetectionCycle", back_populates="step_records")


class VideoClip(Base):
    """视频片段表 - 管理所有录制的视频"""
    __tablename__ = "video_clips"

    id = Column(Integer, primary_key=True, index=True)
    video_uuid = Column(String(50), unique=True, index=True, nullable=False)  # 唯一标识（用于URL）
    
    clip_type = Column(String(20), nullable=False)  # session, cycle, step
    # v3.38 index=True: 清理按归属周期/步骤批量找录像, 老库由 m0001 迁移补建
    related_id = Column(Integer, nullable=True, index=True)  # 关联的记录ID

    # 周期录像的合格结果 (OK / NG / None)。周期结束写库时回写，供"OK/NG 分开存 +
    # 分别保留期"的清理策略使用。session/step 录像无此语义，保持 None。
    result = Column(String(8), nullable=True)
    
    file_path = Column(String(500), nullable=False)  # 文件路径
    file_name = Column(String(200), nullable=True)  # 文件名
    file_size = Column(Integer, default=0)  # 文件大小（字节）
    duration = Column(Float, nullable=True)  # 视频时长（秒）
    
    # 时间信息
    start_time = Column(DateTime(timezone=True), nullable=True)  # 录制开始时间
    end_time = Column(DateTime(timezone=True), nullable=True)  # 录制结束时间
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 元数据
    extra_info = Column(JSON, nullable=True)  # 额外信息


class DataExportSetting(Base):
    """数据导出设置表 - 用户选择要记录/导出的数据项"""
    __tablename__ = "data_export_settings"

    id = Column(Integer, primary_key=True, index=True)
    
    # 记录选项
    record_step_duration = Column(Boolean, default=True)  # 记录步骤耗时
    record_step_interval = Column(Boolean, default=True)  # 记录步骤间隔
    record_cycle_duration = Column(Boolean, default=True)  # 记录周期耗时
    record_cycle_interval = Column(Boolean, default=True)  # 记录周期间隔
    record_avg_step_time = Column(Boolean, default=True)  # 记录平均步骤时间
    record_avg_cycle_time = Column(Boolean, default=True)  # 记录平均周期时间
    record_counters = Column(Boolean, default=True)  # 记录计数器
    
    # 视频录制选项
    record_step_video = Column(Boolean, default=False)  # 录制步骤视频
    record_cycle_video = Column(Boolean, default=False)  # 录制周期视频
    record_session_video = Column(Boolean, default=False)  # 录制会话视频
    
    # 视频质量设置
    video_quality = Column(String(20), default="medium")  # low, medium, high
    video_fps = Column(Integer, default=30)
    
    # 导出选项（与记录选项分开，控制CSV导出内容）
    export_step_duration = Column(Boolean, default=True)  # 导出步骤耗时
    export_step_interval = Column(Boolean, default=True)  # 导出步骤间隔
    export_step_event = Column(Boolean, default=True)  # 导出步骤事件
    export_cycle_duration = Column(Boolean, default=True)  # 导出周期耗时
    export_cycle_interval = Column(Boolean, default=True)  # 导出周期间隔
    export_cycle_result = Column(Boolean, default=True)  # 导出周期结果
    export_counters = Column(Boolean, default=True)  # 导出计数器
    export_session_info = Column(Boolean, default=True)  # 导出会话信息
    
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
