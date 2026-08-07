"""
v3.5.0 自定义导出系统 — 字段元数据中央仓库

所有可导出字段的"单一真相源"。三方消费者：
  1. 前端字段树（拖拽到模板编辑器）        → API: GET /api/v1/export/fields
  2. export_context.py 构造 Jinja2 上下文   → 必须保证每个字段在 context 里都有值（缺失给 None）
  3. 模板预览/校验时报错"未知字段 xxx"      → 用 lookup_field(path) 检查

字段命名规则（重要！前端拖拽生成的 Jinja2 表达式直接用 path）：
  - 标量      : "cycle.is_good"           → {{ cycle.is_good }}
  - 数组      : "steps[*].label"          → {% for s in steps %}{{ s.label }}{% endfor %}
  - 字典 KV   : "counters.{name}"         → {{ counters['ng_count'] }}
  - 嵌套字典  : "mes.workpiece.barcode"   → {{ mes.workpiece.barcode }}

新增字段流程：
  1. 在对应 _GROUP_xxx 列表里加一个 _f(...)
  2. 在 export_context.py 对应构造函数里把数据填进去
  3. 重启后端，前端字段树自动有了
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional


# ============================================================
# 类型常量 / 分组常量
# ============================================================

# 字段类型 — 前端字段树根据它给图标和拖拽时的 Jinja2 模板提示
TYPE_STR = "str"
TYPE_INT = "int"
TYPE_FLOAT = "float"
TYPE_BOOL = "bool"
TYPE_DATETIME = "datetime"
TYPE_LIST = "list"           # path 末尾带 [*] 的数组，需要 {% for %}
TYPE_DICT_KV = "dict_kv"     # path 末尾带 {name} 的 KV，需要 dict 访问
TYPE_DICT = "dict"           # 子树根（占位，本身不能拖拽）
TYPE_JSON = "json"           # 任意结构（配置类）
TYPE_ENUM = "enum"


# 分组 ID（前端字段树第一层）
GROUP_APP = "app"
GROUP_SYSTEM = "system"
GROUP_LICENSE = "license"
GROUP_DISPLAY = "display"
GROUP_PROJECT = "project"
GROUP_CHANNEL = "channel"
GROUP_SESSION = "session"
GROUP_CYCLE = "cycle"
GROUP_STEPS = "steps"
GROUP_WORKPIECE = "workpiece"
GROUP_WORK_ORDER = "order"
GROUP_OPERATOR = "operator"
GROUP_DEFECTS = "defects"
GROUP_BOX = "box"
GROUP_LIVE = "live"
GROUP_LIVE_TRACKING = "live_tracking"
GROUP_COUNTERS = "counters"
GROUP_COUNTERS_DAILY = "counters_daily"
GROUP_MES = "mes"
GROUP_SCANNER = "scanner"
GROUP_STATS = "stats"
GROUP_AGGREGATIONS = "aggregations"
GROUP_TIME = "time"
GROUP_PLUGIN = "plugin"      # v3.46 F8: 插件注册的动态字段（path 前缀 plugin.<cc>.）


GROUP_LABELS: Dict[str, str] = {
    GROUP_APP: "软件信息",
    GROUP_SYSTEM: "系统/硬件",
    GROUP_LICENSE: "License",
    GROUP_DISPLAY: "品牌/显示",
    GROUP_PROJECT: "项目配置",
    GROUP_CHANNEL: "通道/工位",
    GROUP_SESSION: "会话(Session)",
    GROUP_CYCLE: "周期(Cycle)",
    GROUP_STEPS: "步骤(Steps数组)",
    GROUP_WORKPIECE: "工件(Workpiece)",
    GROUP_WORK_ORDER: "工单(Order)",
    GROUP_OPERATOR: "操作员",
    GROUP_DEFECTS: "缺陷(数组)",
    GROUP_BOX: "集箱聚合(Box)",
    GROUP_LIVE: "实时检测",
    GROUP_LIVE_TRACKING: "实时追踪状态",
    GROUP_COUNTERS: "计数器",
    GROUP_COUNTERS_DAILY: "计数器(当日增量)",
    GROUP_MES: "MES 上下文",
    GROUP_SCANNER: "扫码/外设",
    GROUP_STATS: "Session 聚合统计",
    GROUP_AGGREGATIONS: "跨 Session 聚合",
    GROUP_TIME: "时间/日期",
    GROUP_PLUGIN: "插件字段",
}

# 分组在前端字段树中的顺序
GROUPS_DISPLAY_ORDER: List[str] = [
    GROUP_TIME, GROUP_APP, GROUP_DISPLAY, GROUP_LICENSE, GROUP_SYSTEM,
    GROUP_CHANNEL, GROUP_PROJECT,
    GROUP_SESSION, GROUP_CYCLE, GROUP_STEPS, GROUP_DEFECTS,
    GROUP_WORKPIECE, GROUP_WORK_ORDER, GROUP_OPERATOR,
    GROUP_BOX, GROUP_SCANNER, GROUP_MES,
    GROUP_LIVE, GROUP_LIVE_TRACKING, GROUP_COUNTERS, GROUP_COUNTERS_DAILY,
    GROUP_STATS, GROUP_AGGREGATIONS,
    GROUP_PLUGIN,
]


# ============================================================
# 数据结构
# ============================================================

@dataclass
class FieldDef:
    path: str
    label: str
    type: str
    group: str
    example: str = ""
    notes: str = ""
    format_hint: str = ""           # 如: "{{ x | round(2) }}"
    enum_values: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)   # 数据来源场景: cycle/range/system
    available_in: List[str] = field(default_factory=list)  # batch / realtime / both
    deprecated: bool = False


def _f(path: str, label: str, type_: str, group: str,
       example: str = "", notes: str = "",
       format_hint: str = "",
       enum_values: Optional[List[str]] = None,
       sources: Optional[List[str]] = None,
       available_in: Optional[List[str]] = None) -> FieldDef:
    """字段定义工厂 — 简化书写"""
    return FieldDef(
        path=path, label=label, type=type_, group=group,
        example=example, notes=notes, format_hint=format_hint,
        enum_values=enum_values or [],
        sources=sources or ["cycle", "range"],
        available_in=available_in or ["batch", "realtime"],
    )


# ============================================================
# 时间字段（特殊 — 任何场景都可以用）
# ============================================================
_GROUP_TIME: List[FieldDef] = [
    _f("now", "当前时间(完整)", TYPE_DATETIME, GROUP_TIME, "2026-05-03T17:53:21.123+08:00",
       "渲染瞬间的 datetime，不是 cycle 时间", "{{ now.strftime('%Y-%m-%d %H:%M:%S') }}"),
    _f("now_date", "当前日期", TYPE_STR, GROUP_TIME, "2026-05-03"),
    _f("now_time", "当前时分秒", TYPE_STR, GROUP_TIME, "17:53:21"),
    _f("now_ymdhms", "时间戳(紧凑)", TYPE_STR, GROUP_TIME, "20260503_175321",
       "用于文件名 — 安全可读"),
    _f("now_unix", "Unix 时间戳(秒)", TYPE_INT, GROUP_TIME, "1746263601"),
    _f("now_unix_ms", "Unix 时间戳(毫秒)", TYPE_INT, GROUP_TIME, "1746263601123"),
    _f("now_iso", "ISO 8601", TYPE_STR, GROUP_TIME, "2026-05-03T17:53:21.123+08:00"),
    _f("today_shift", "当前班次", TYPE_STR, GROUP_TIME, "day", "day/night, 由系统判定"),
]


# ============================================================
# app.* / display.* / license.* / system.*
# ============================================================
_GROUP_APP_FIELDS: List[FieldDef] = [
    _f("app.name", "软件名称", TYPE_STR, GROUP_APP, "天骏视觉", "package.json: productName"),
    _f("app.version", "软件版本号", TYPE_STR, GROUP_APP, "3.4.1", "来自 electron/package.json: version"),
    _f("app.product_name", "产品名(英文)", TYPE_STR, GROUP_APP, "Tianjun Vision"),
    _f("app.brand_name", "品牌名", TYPE_STR, GROUP_APP, "天骏"),
    _f("app.build_date", "构建日期", TYPE_STR, GROUP_APP, "2026-04-12"),
    _f("app.commit_hash", "Git Commit", TYPE_STR, GROUP_APP, "a1b2c3d", "可选 — CI 注入"),
]

_GROUP_DISPLAY_FIELDS: List[FieldDef] = [
    _f("display.brand_name", "品牌名", TYPE_STR, GROUP_DISPLAY, "天骏视觉",
       "前端 useSystemStore.display.brandName，后端 SystemConfig 同步"),
    _f("display.app_name", "应用展示名", TYPE_STR, GROUP_DISPLAY, "智能检测平台"),
    _f("display.inspector_name", "检测员/工位标识", TYPE_STR, GROUP_DISPLAY, "工位01"),
    _f("display.device_number", "设备编号", TYPE_STR, GROUP_DISPLAY, "TJV-2026-001"),
    _f("display.factory_name", "工厂名称", TYPE_STR, GROUP_DISPLAY, "南通工厂A线"),
    _f("display.line_name", "产线名称", TYPE_STR, GROUP_DISPLAY, "SMT-1"),
]

_GROUP_LICENSE_FIELDS: List[FieldDef] = [
    _f("license.customer", "授权客户名", TYPE_STR, GROUP_LICENSE, "ABC科技有限公司",
       "Electron license-manager 解析"),
    _f("license.machine_id", "机器码", TYPE_STR, GROUP_LICENSE, "MID-XXXX-YYYY"),
    _f("license.expires_at", "有效期至", TYPE_DATETIME, GROUP_LICENSE, "2027-12-31"),
    _f("license.is_perpetual", "永久授权", TYPE_BOOL, GROUP_LICENSE, "false"),
    _f("license.days_remaining", "剩余天数", TYPE_INT, GROUP_LICENSE, "365"),
    _f("license.features", "授权功能列表", TYPE_LIST, GROUP_LICENSE, "['mes', 'cluster']"),
]

_GROUP_SYSTEM_FIELDS: List[FieldDef] = [
    _f("system.hostname", "主机名", TYPE_STR, GROUP_SYSTEM, "tianjun-pc-01"),
    _f("system.os", "操作系统", TYPE_STR, GROUP_SYSTEM, "Windows 10 Pro"),
    _f("system.cpu", "CPU", TYPE_STR, GROUP_SYSTEM, "Intel i7-12700"),
    _f("system.gpu", "GPU", TYPE_STR, GROUP_SYSTEM, "NVIDIA RTX 4060 Ti"),
    _f("system.gpu_count", "GPU 数量", TYPE_INT, GROUP_SYSTEM, "1"),
    _f("system.memory_gb", "内存(GB)", TYPE_FLOAT, GROUP_SYSTEM, "32.0"),
    _f("system.disk_free_gb", "可用磁盘(GB)", TYPE_FLOAT, GROUP_SYSTEM, "512.5"),
    _f("system.uptime_seconds", "运行时长(秒)", TYPE_INT, GROUP_SYSTEM, "86400"),
    _f("system.data_dir", "数据目录", TYPE_STR, GROUP_SYSTEM, "C:/tianjun/data"),
]


# ============================================================
# project.*
# ============================================================
_GROUP_PROJECT_FIELDS: List[FieldDef] = [
    _f("project.id", "项目 ID", TYPE_INT, GROUP_PROJECT, "12"),
    _f("project.name", "项目名称", TYPE_STR, GROUP_PROJECT, "电池极耳检测"),
    _f("project.task_type", "任务类型", TYPE_ENUM, GROUP_PROJECT, "detection",
       enum_values=["detection", "segmentation", "classification"]),
    _f("project.logic_mode", "判定逻辑模式", TYPE_ENUM, GROUP_PROJECT, "sequential",
       enum_values=["sequential", "detection", "custom"]),
    _f("project.model_format", "推理格式", TYPE_ENUM, GROUP_PROJECT, "tensorrt_fp16",
       enum_values=["pytorch_fp32", "pytorch_fp16", "onnx", "tensorrt_fp32",
                    "tensorrt_fp16", "tensorrt_int8"]),
    _f("project.model_id", "默认模型 ID", TYPE_INT, GROUP_PROJECT, "5"),
    _f("project.model_name", "默认模型名", TYPE_STR, GROUP_PROJECT, "yolov8n_battery_v2"),
    _f("project.model_version", "模型版本", TYPE_STR, GROUP_PROJECT, "v2.1"),
    _f("project.model_file", "模型文件名", TYPE_STR, GROUP_PROJECT, "battery_v2.1.engine"),
    _f("project.is_active", "当前激活", TYPE_BOOL, GROUP_PROJECT, "true"),
    _f("project.created_at", "项目创建时间", TYPE_DATETIME, GROUP_PROJECT, "2026-03-15"),
    _f("project.updated_at", "项目最近修改", TYPE_DATETIME, GROUP_PROJECT, "2026-04-30"),
    _f("project.steps_count", "步骤数量", TYPE_INT, GROUP_PROJECT, "5"),
    _f("project.events_count", "事件数量", TYPE_INT, GROUP_PROJECT, "3"),
    _f("project.counters_count", "计数器数量", TYPE_INT, GROUP_PROJECT, "2"),
    _f("project.steps_config", "步骤配置(完整 JSON)", TYPE_JSON, GROUP_PROJECT,
       "[{...}, {...}]", "用 {{ project.steps_config | tojson }} 输出原始 JSON"),
    _f("project.events_config", "事件配置(JSON)", TYPE_JSON, GROUP_PROJECT, "[...]"),
    _f("project.counters_config", "计数器配置(JSON)", TYPE_JSON, GROUP_PROJECT, "[...]"),
    _f("project.alarm_config", "报警配置(JSON)", TYPE_JSON, GROUP_PROJECT, "{...}"),
    _f("project.detection_config", "检测框配置(JSON)", TYPE_JSON, GROUP_PROJECT, "{...}"),
    _f("project.data_config", "数据导出配置(JSON)", TYPE_JSON, GROUP_PROJECT, "{...}"),
    _f("project.pipeline_config", "推理管线配置(JSON)", TYPE_JSON, GROUP_PROJECT, "{...}"),
    _f("project.confidence_threshold", "置信度阈值", TYPE_FLOAT, GROUP_PROJECT, "0.45",
       "从 pipeline_config 读"),
    _f("project.iou_threshold", "IOU 阈值", TYPE_FLOAT, GROUP_PROJECT, "0.5"),
    _f("project.imgsz", "推理分辨率", TYPE_INT, GROUP_PROJECT, "640"),
]


# ============================================================
# channel.* (当前通道/工位信息)
# ============================================================
_GROUP_CHANNEL_FIELDS: List[FieldDef] = [
    _f("channel.id", "通道 ID(0 起)", TYPE_INT, GROUP_CHANNEL, "0"),
    _f("channel.name", "通道名称", TYPE_STR, GROUP_CHANNEL, "工位1"),
    _f("channel.station_id", "集群站点 ID", TYPE_STR, GROUP_CHANNEL, "A",
       "仅集群模式下有"),
    _f("channel.video_source_type", "视频源类型", TYPE_ENUM, GROUP_CHANNEL, "rtsp",
       enum_values=["camera", "video_file", "image", "rtsp", "hikrobot", "mvs"]),
    _f("channel.video_source_url", "视频源 URL", TYPE_STR, GROUP_CHANNEL,
       "rtsp://192.168.1.10:554/stream"),
    _f("channel.resolution_w", "采集分辨率宽", TYPE_INT, GROUP_CHANNEL, "1920"),
    _f("channel.resolution_h", "采集分辨率高", TYPE_INT, GROUP_CHANNEL, "1080"),
    _f("channel.gpu_index", "占用 GPU 编号", TYPE_INT, GROUP_CHANNEL, "0"),
]


# ============================================================
# session.*
# ============================================================
_GROUP_SESSION_FIELDS: List[FieldDef] = [
    _f("session.id", "会话 ID", TYPE_INT, GROUP_SESSION, "1024"),
    _f("session.session_uuid", "会话 UUID", TYPE_STR, GROUP_SESSION, "abc-123-def"),
    _f("session.name", "会话名(可选)", TYPE_STR, GROUP_SESSION, "Day-2026-05-03"),
    _f("session.channel_id", "通道 ID", TYPE_INT, GROUP_SESSION, "0"),
    _f("session.shift_label", "班次", TYPE_ENUM, GROUP_SESSION, "day",
       enum_values=["day", "night"]),
    _f("session.status", "会话状态", TYPE_ENUM, GROUP_SESSION, "running",
       enum_values=["running", "completed", "interrupted", "stopped"]),
    _f("session.start_time", "开始时间", TYPE_DATETIME, GROUP_SESSION, "2026-05-03T08:00:00"),
    _f("session.end_time", "结束时间", TYPE_DATETIME, GROUP_SESSION, "2026-05-03T17:30:00"),
    _f("session.duration", "总时长(秒)", TYPE_FLOAT, GROUP_SESSION, "34200.5"),
    _f("session.duration_human", "总时长(可读)", TYPE_STR, GROUP_SESSION, "9h 30m 0s"),
    _f("session.total_cycles", "总周期数", TYPE_INT, GROUP_SESSION, "856"),
    _f("session.good_cycles", "良品周期数", TYPE_INT, GROUP_SESSION, "823"),
    _f("session.ng_cycles", "NG 周期数", TYPE_INT, GROUP_SESSION, "33"),
    _f("session.yield_rate", "良率(%)", TYPE_FLOAT, GROUP_SESSION, "96.14",
       "100*good/total，已乘 100"),
    _f("session.yield_ratio", "良率(0~1)", TYPE_FLOAT, GROUP_SESSION, "0.9614"),
    _f("session.avg_cycle_time", "平均周期时长(秒)", TYPE_FLOAT, GROUP_SESSION, "12.45"),
    _f("session.min_cycle_time", "最短周期时长(秒)", TYPE_FLOAT, GROUP_SESSION, "8.21"),
    _f("session.max_cycle_time", "最长周期时长(秒)", TYPE_FLOAT, GROUP_SESSION, "18.93"),
    _f("session.avg_cycle_interval", "平均周期间隔(秒)", TYPE_FLOAT, GROUP_SESSION, "3.12"),
    _f("session.project_id", "关联项目 ID", TYPE_INT, GROUP_SESSION, "12"),
    _f("session.operator_id", "关联操作员 ID", TYPE_INT, GROUP_SESSION, "3"),
    _f("session.order_id", "关联工单 ID", TYPE_INT, GROUP_SESSION, "88"),
]


# ============================================================
# cycle.*
# ============================================================
_GROUP_CYCLE_FIELDS: List[FieldDef] = [
    _f("cycle.id", "周期 ID", TYPE_INT, GROUP_CYCLE, "10056"),
    _f("cycle.session_id", "所属 session ID", TYPE_INT, GROUP_CYCLE, "1024"),
    _f("cycle.channel_id", "通道 ID", TYPE_INT, GROUP_CYCLE, "0"),
    _f("cycle.cycle_number", "周期序号", TYPE_INT, GROUP_CYCLE, "37",
       "session 内从 1 开始"),
    _f("cycle.start_time", "周期开始", TYPE_DATETIME, GROUP_CYCLE, "2026-05-03T17:50:12.345"),
    _f("cycle.end_time", "周期结束", TYPE_DATETIME, GROUP_CYCLE, "2026-05-03T17:50:24.789"),
    _f("cycle.duration", "周期时长(秒)", TYPE_FLOAT, GROUP_CYCLE, "12.444"),
    _f("cycle.duration_ms", "周期时长(毫秒)", TYPE_INT, GROUP_CYCLE, "12444"),
    _f("cycle.interval_to_next", "到下一周期间隔(秒)", TYPE_FLOAT, GROUP_CYCLE, "3.21",
       "末轮 cycle 此值为 None"),
    _f("cycle.is_good", "是否良品", TYPE_BOOL, GROUP_CYCLE, "true"),
    _f("cycle.result", "结果(中文)", TYPE_STR, GROUP_CYCLE, "良品",
       "良品 / NG / 中断"),
    _f("cycle.result_pass_fail", "结果(英文)", TYPE_STR, GROUP_CYCLE, "Pass",
       "Pass / Fail — 用于 SN.txt 等英文格式"),
    _f("cycle.ng_step", "首个 NG 步骤名", TYPE_STR, GROUP_CYCLE, "焊接检测",
       "若全部通过，为空"),
    _f("cycle.ng_step_index", "首个 NG 步骤序号(0 起)", TYPE_INT, GROUP_CYCLE, "2"),
    _f("cycle.ng_reason", "NG 原因", TYPE_STR, GROUP_CYCLE, "未检测到目标"),
    _f("cycle.ng_code", "NG 编码", TYPE_STR, GROUP_CYCLE, "C0547",
       "项目配置里给每个 NG 步骤映射 NG 码（无映射时空）"),
    _f("cycle.total_steps", "总步骤数", TYPE_INT, GROUP_CYCLE, "5"),
    _f("cycle.good_steps", "通过步骤数", TYPE_INT, GROUP_CYCLE, "4"),
    _f("cycle.ng_steps_count", "失败步骤数", TYPE_INT, GROUP_CYCLE, "1"),
    _f("cycle.recognized_max", "本轮识别峰值", TYPE_INT, GROUP_CYCLE, "8",
       "tracking 模式下本周期识别到的最高目标数"),
    _f("cycle.video_clip_path", "录像文件路径", TYPE_STR, GROUP_CYCLE,
       "videos/2026-05-03/cycle_10056.mp4"),
    _f("cycle.video_clip_url", "录像 URL", TYPE_STR, GROUP_CYCLE,
       "/api/v1/source/videos/.../cycle_10056.mp4"),
    _f("cycle.test_values", "测试值数组(自定义)", TYPE_LIST, GROUP_CYCLE,
       "[0.01, 0.0206, 0.05, 0.0006]",
       "项目配置里把检测结果映射到的浮点数列表，供 SN.txt 等固定表头格式使用"),
    _f("cycle.barcode", "工件条码(冗余)", TYPE_STR, GROUP_CYCLE, "8SSC21K6...",
       "等同 cycle.workpiece.serial_no，方便顶层引用"),
]


# ============================================================
# steps[*].* （数组）
# ============================================================
_GROUP_STEPS_FIELDS: List[FieldDef] = [
    _f("steps[*]", "步骤数组(根)", TYPE_LIST, GROUP_STEPS, "[step1, step2, ...]",
       "{% for step in steps %}...{% endfor %}"),
    _f("steps[*].index", "步骤序号(0 起)", TYPE_INT, GROUP_STEPS, "0"),
    _f("steps[*].label", "步骤名/标签", TYPE_STR, GROUP_STEPS, "焊接检测"),
    _f("steps[*].is_good", "是否通过", TYPE_BOOL, GROUP_STEPS, "true"),
    _f("steps[*].result", "结果(中文)", TYPE_STR, GROUP_STEPS, "OK"),
    _f("steps[*].result_pass_fail", "结果(英文)", TYPE_STR, GROUP_STEPS, "Pass"),
    _f("steps[*].duration", "步骤时长(秒)", TYPE_FLOAT, GROUP_STEPS, "2.45"),
    _f("steps[*].interval_to_next", "到下一步间隔(秒)", TYPE_FLOAT, GROUP_STEPS, "0.5"),
    _f("steps[*].confidence", "置信度", TYPE_FLOAT, GROUP_STEPS, "0.875",
       "StepRecord.confidence — 步骤结束瞬间的代表值。"
       "若需跨周期 max/min/avg 见 stats.step_averages[*].*"),
    _f("steps[*].frame_count", "经过帧数", TYPE_INT, GROUP_STEPS, "47"),
    _f("steps[*].start_time", "步骤开始", TYPE_DATETIME, GROUP_STEPS,
       "2026-05-03T17:50:12.345"),
    _f("steps[*].end_time", "步骤结束", TYPE_DATETIME, GROUP_STEPS,
       "2026-05-03T17:50:14.795"),
    _f("steps[*].event", "事件类型", TYPE_STR, GROUP_STEPS, "auto",
       "auto / manual / timeout / skipped"),
    _f("steps[*].ng_reason", "NG 原因", TYPE_STR, GROUP_STEPS, "时间不足"),
]


# ============================================================
# workpiece.*  (扫码绑定的工件)
# ============================================================
_GROUP_WORKPIECE_FIELDS: List[FieldDef] = [
    _f("workpiece.id", "工件 ID(数据库)", TYPE_INT, GROUP_WORKPIECE, "55321"),
    _f("workpiece.serial_no", "条码/SN", TYPE_STR, GROUP_WORKPIECE,
       "8SSC21K64645C1WJ63D2T2G", "扫码器读到的字符串"),
    _f("workpiece.batch_no", "批次号", TYPE_STR, GROUP_WORKPIECE, "BATCH-20260503-001"),
    _f("workpiece.product_code", "产品料号", TYPE_STR, GROUP_WORKPIECE, "P-1234"),
    _f("workpiece.product_name", "产品名称", TYPE_STR, GROUP_WORKPIECE, "电池模组"),
    _f("workpiece.status", "工件状态", TYPE_ENUM, GROUP_WORKPIECE, "good",
       enum_values=["pending", "good", "ng", "rework", "scrap"]),
    _f("workpiece.is_good", "是否良品", TYPE_BOOL, GROUP_WORKPIECE, "true"),
    _f("workpiece.first_seen_at", "首次扫码时间", TYPE_DATETIME, GROUP_WORKPIECE,
       "2026-05-03T17:48:00"),
    _f("workpiece.last_inspection_at", "最近检测时间", TYPE_DATETIME, GROUP_WORKPIECE,
       "2026-05-03T17:50:24"),
    _f("workpiece.inspection_count", "检测次数", TYPE_INT, GROUP_WORKPIECE, "1"),
    _f("workpiece.rework_count", "返工次数", TYPE_INT, GROUP_WORKPIECE, "0"),
    _f("workpiece.defect_count", "缺陷数量", TYPE_INT, GROUP_WORKPIECE, "0"),
    _f("workpiece.barcode_parse", "条码解析(JSON)", TYPE_JSON, GROUP_WORKPIECE,
       "{model: 'M1', date: '20260503'}", "barcode_parser 解析结果"),
    _f("workpiece.work_order_id", "关联工单 ID", TYPE_INT, GROUP_WORKPIECE, "88"),
    _f("workpiece.order_no", "关联工单号", TYPE_STR, GROUP_WORKPIECE, "WO-20260503-A1"),
]


# ============================================================
# order.*  （工单）
# ============================================================
_GROUP_ORDER_FIELDS: List[FieldDef] = [
    _f("order.id", "工单 ID", TYPE_INT, GROUP_WORK_ORDER, "88"),
    _f("order.order_no", "工单号", TYPE_STR, GROUP_WORK_ORDER, "WO-20260503-A1"),
    _f("order.external_id", "外部 MES ID", TYPE_STR, GROUP_WORK_ORDER, "MES-X-001"),
    _f("order.product_name", "产品名称", TYPE_STR, GROUP_WORK_ORDER, "电池模组"),
    _f("order.product_code", "产品料号", TYPE_STR, GROUP_WORK_ORDER, "P-1234"),
    _f("order.product_spec", "产品规格", TYPE_STR, GROUP_WORK_ORDER, "Type-A 大电流版"),
    _f("order.planned_qty", "计划数量", TYPE_INT, GROUP_WORK_ORDER, "1000"),
    _f("order.completed_qty", "完成数量", TYPE_INT, GROUP_WORK_ORDER, "843"),
    _f("order.good_qty", "良品数", TYPE_INT, GROUP_WORK_ORDER, "823"),
    _f("order.ng_qty", "NG 数", TYPE_INT, GROUP_WORK_ORDER, "20"),
    _f("order.rework_qty", "返工数", TYPE_INT, GROUP_WORK_ORDER, "5"),
    _f("order.scrap_qty", "报废数", TYPE_INT, GROUP_WORK_ORDER, "2"),
    _f("order.yield_rate", "良率(0~1)", TYPE_FLOAT, GROUP_WORK_ORDER, "0.9764"),
    _f("order.progress_rate", "完成率(0~1)", TYPE_FLOAT, GROUP_WORK_ORDER, "0.843"),
    _f("order.priority", "优先级", TYPE_INT, GROUP_WORK_ORDER, "3"),
    _f("order.status", "状态", TYPE_ENUM, GROUP_WORK_ORDER, "in_progress",
       enum_values=["draft", "pending", "in_progress", "paused", "completed", "cancelled"]),
    _f("order.binding_scope", "绑定范围", TYPE_ENUM, GROUP_WORK_ORDER, "project",
       enum_values=["project", "channels", "cluster"]),
    _f("order.target_channels", "目标通道列表", TYPE_LIST, GROUP_WORK_ORDER, "[0, 1]"),
    _f("order.target_stations", "目标站点列表", TYPE_LIST, GROUP_WORK_ORDER, "['A', 'B']"),
    _f("order.source", "工单来源", TYPE_ENUM, GROUP_WORK_ORDER, "manual",
       enum_values=["manual", "external_mes"]),
    _f("order.planned_start", "计划开始时间", TYPE_DATETIME, GROUP_WORK_ORDER, "2026-05-03T08:00"),
    _f("order.planned_end", "计划结束时间", TYPE_DATETIME, GROUP_WORK_ORDER, "2026-05-04T18:00"),
    _f("order.actual_start", "实际开始时间", TYPE_DATETIME, GROUP_WORK_ORDER, "2026-05-03T08:15"),
    _f("order.actual_end", "实际结束时间", TYPE_DATETIME, GROUP_WORK_ORDER, "2026-05-03T17:45"),
    _f("order.created_at", "创建时间", TYPE_DATETIME, GROUP_WORK_ORDER, "2026-05-02"),
    _f("order.updated_at", "更新时间", TYPE_DATETIME, GROUP_WORK_ORDER, "2026-05-03"),
]


# ============================================================
# operator.*
# ============================================================
_GROUP_OPERATOR_FIELDS: List[FieldDef] = [
    _f("operator.id", "操作员 ID", TYPE_INT, GROUP_OPERATOR, "3"),
    _f("operator.name", "姓名", TYPE_STR, GROUP_OPERATOR, "张三"),
    _f("operator.employee_no", "工号", TYPE_STR, GROUP_OPERATOR, "E10042"),
    _f("operator.role", "角色", TYPE_ENUM, GROUP_OPERATOR, "operator",
       enum_values=["operator", "leader", "qc", "admin"]),
    _f("operator.shift", "当前班次", TYPE_STR, GROUP_OPERATOR, "day"),
    _f("operator.login_time", "登录时间", TYPE_DATETIME, GROUP_OPERATOR, "2026-05-03T08:00"),
]


# ============================================================
# defects[*].*
# ============================================================
_GROUP_DEFECTS_FIELDS: List[FieldDef] = [
    _f("defects[*]", "缺陷数组(根)", TYPE_LIST, GROUP_DEFECTS, "[d1, d2]",
       "{% for d in defects %}...{% endfor %}"),
    _f("defects[*].id", "缺陷 ID", TYPE_INT, GROUP_DEFECTS, "9981"),
    _f("defects[*].defect_type", "缺陷类型/类目", TYPE_STR, GROUP_DEFECTS, "划痕"),
    _f("defects[*].defect_code", "缺陷代码", TYPE_STR, GROUP_DEFECTS, "C0547"),
    _f("defects[*].severity", "严重等级", TYPE_ENUM, GROUP_DEFECTS, "major",
       enum_values=["minor", "major", "critical"]),
    _f("defects[*].confidence", "置信度", TYPE_FLOAT, GROUP_DEFECTS, "0.823"),
    _f("defects[*].position", "位置/坐标", TYPE_STR, GROUP_DEFECTS, "(120, 340)"),
    _f("defects[*].step_label", "出现在步骤", TYPE_STR, GROUP_DEFECTS, "外观检测"),
    _f("defects[*].source", "数据源", TYPE_ENUM, GROUP_DEFECTS, "auto",
       enum_values=["auto", "manual"]),
    _f("defects[*].operator_id", "登记人(手动时)", TYPE_INT, GROUP_DEFECTS, "3"),
    _f("defects[*].created_at", "登记时间", TYPE_DATETIME, GROUP_DEFECTS,
       "2026-05-03T17:50:24"),
    _f("defects[*].image_path", "缺陷截图", TYPE_STR, GROUP_DEFECTS,
       "defects/d_9981.jpg"),
    _f("defects[*].notes", "备注", TYPE_STR, GROUP_DEFECTS, "客户已确认"),
]


# ============================================================
# box.*  （集箱聚合 — 集群模式）
# ============================================================
_GROUP_BOX_FIELDS: List[FieldDef] = [
    _f("box.id", "Box ID", TYPE_INT, GROUP_BOX, "504"),
    _f("box.box_serial", "箱条码", TYPE_STR, GROUP_BOX, "BOX-20260503-A-001"),
    _f("box.status", "状态", TYPE_ENUM, GROUP_BOX, "complete",
       enum_values=["pending", "in_progress", "complete", "timeout"]),
    _f("box.target_count", "目标件数", TYPE_INT, GROUP_BOX, "20"),
    _f("box.actual_count", "实到件数", TYPE_INT, GROUP_BOX, "20"),
    _f("box.good_count", "良品数", TYPE_INT, GROUP_BOX, "19"),
    _f("box.ng_count", "NG 数", TYPE_INT, GROUP_BOX, "1"),
    _f("box.is_good", "整箱是否合格", TYPE_BOOL, GROUP_BOX, "false"),
    _f("box.stations_reported", "已上报站点", TYPE_LIST, GROUP_BOX, "['A', 'B']"),
    _f("box.station_results", "各站点结果", TYPE_DICT_KV, GROUP_BOX,
       "{A: 'good', B: 'ng'}"),
    _f("box.start_time", "开始时间", TYPE_DATETIME, GROUP_BOX, "2026-05-03T17:30:00"),
    _f("box.end_time", "完成时间", TYPE_DATETIME, GROUP_BOX, "2026-05-03T17:45:00"),
    _f("box.duration", "总时长(秒)", TYPE_FLOAT, GROUP_BOX, "900.0"),
    _f("box.workpieces", "包含工件 SN 列表", TYPE_LIST, GROUP_BOX,
       "['SN1', 'SN2']"),
]


# ============================================================
# scanner.*  （绑定的扫码器/外设）
# ============================================================
_GROUP_SCANNER_FIELDS: List[FieldDef] = [
    _f("scanner.last_barcode", "最近条码", TYPE_STR, GROUP_SCANNER,
       "8SSC21K6..."),
    _f("scanner.last_scan_time", "最近扫码时间", TYPE_DATETIME, GROUP_SCANNER,
       "2026-05-03T17:50:00"),
    _f("scanner.scan_count_today", "今日扫码次数", TYPE_INT, GROUP_SCANNER, "856"),
    _f("scanner.duplicate_scan_count", "重复扫码次数", TYPE_INT, GROUP_SCANNER, "12"),
    _f("scanner.connected", "连接状态", TYPE_BOOL, GROUP_SCANNER, "true"),
    _f("scanner.device_name", "设备名", TYPE_STR, GROUP_SCANNER, "192.168.1.2"),
    _f("scanner.device_ip", "设备 IP", TYPE_STR, GROUP_SCANNER, "192.168.1.2"),
    _f("scanner.scan_mode", "扫描模式", TYPE_ENUM, GROUP_SCANNER, "continuous",
       enum_values=["continuous", "interval", "external"]),
    _f("scanner.last_weight", "最近称重值(g)", TYPE_FLOAT, GROUP_SCANNER, "256.4"),
    _f("scanner.last_external_value", "最近外设原始值", TYPE_STR, GROUP_SCANNER, "256.4"),
]


# ============================================================
# live.* （仅实时场景；批量场景用上一周期值兜底）
# ============================================================
_GROUP_LIVE_FIELDS: List[FieldDef] = [
    _f("live.fps", "采集 FPS", TYPE_FLOAT, GROUP_LIVE, "30.1",
       sources=["cycle"], available_in=["realtime"]),
    _f("live.fps_inference", "推理 FPS", TYPE_FLOAT, GROUP_LIVE, "12.8",
       "v2.7.13+，比 fps 更准确反映模型负载"),
    _f("live.latency_ms", "推理延迟(ms)", TYPE_FLOAT, GROUP_LIVE, "78.2"),
    _f("live.frame_index", "当前帧编号", TYPE_INT, GROUP_LIVE, "10523"),
    _f("live.detections", "当前帧检测结果数组(根)", TYPE_LIST, GROUP_LIVE,
       "[{x,y,w,h,label,conf}, ...]"),
    _f("live.detections[*].label", "检测标签", TYPE_STR, GROUP_LIVE, "battery"),
    _f("live.detections[*].confidence", "置信度", TYPE_FLOAT, GROUP_LIVE, "0.92"),
    _f("live.detections[*].x", "归一化 X(左上)", TYPE_FLOAT, GROUP_LIVE, "0.32",
       "0~1 归一化值，乘 frame_w 得像素"),
    _f("live.detections[*].y", "归一化 Y(左上)", TYPE_FLOAT, GROUP_LIVE, "0.41"),
    _f("live.detections[*].w", "归一化宽", TYPE_FLOAT, GROUP_LIVE, "0.15"),
    _f("live.detections[*].h", "归一化高", TYPE_FLOAT, GROUP_LIVE, "0.22"),
    _f("live.detections[*].track_id", "追踪 ID", TYPE_INT, GROUP_LIVE, "7"),
    _f("live.detections[*].class_id", "类别索引", TYPE_INT, GROUP_LIVE, "0"),
    _f("live.frame_w", "帧宽", TYPE_INT, GROUP_LIVE, "1920"),
    _f("live.frame_h", "帧高", TYPE_INT, GROUP_LIVE, "1080"),
    _f("live.recent_events", "最近事件列表", TYPE_LIST, GROUP_LIVE,
       "[{type:'auto_ok', step:'焊接', time:...}]"),
    _f("live.last_event_type", "最近事件类型", TYPE_STR, GROUP_LIVE, "auto_ok"),
    _f("live.last_event_step", "最近事件步骤", TYPE_STR, GROUP_LIVE, "焊接检测"),
    _f("live.last_event_time", "最近事件时间", TYPE_DATETIME, GROUP_LIVE,
       "2026-05-03T17:50:23.456"),
    _f("live.is_inferring", "正在推理", TYPE_BOOL, GROUP_LIVE, "true"),
    _f("live.is_recording", "正在录像", TYPE_BOOL, GROUP_LIVE, "true"),
    _f("live.current_step_index", "当前步骤序号", TYPE_INT, GROUP_LIVE, "2"),
    _f("live.current_step_label", "当前步骤名", TYPE_STR, GROUP_LIVE, "焊接检测"),
    _f("live.current_step_elapsed", "当前步骤已用时(秒)", TYPE_FLOAT, GROUP_LIVE, "1.23"),
]


# ============================================================
# live_tracking.* （Step 1.5 用户决定 5A 暴露的内部状态）
#
# 字段命名贴近 source 实际状态变量：
#   _stack_state / _stack_counters         → stack_states / stack_counters
#   _tracking_objects                       → active_count / tracked_objects
#   _tracking_class_counters                → class_counters
#   _tracking_locked_ids                    → locked_count
#   _tracking_recently_lost                 → lost_count
#   _tracking_prev_count / _tracking_was_complete → prev_count / was_complete
#   _event_counters / _event_state          → event_counters / event_states
#   _box_objects / _box_counter / _box_settled_results → boxes / box_counter / settled_*
#   _scan_d_armed_box                       → scan_d_armed_box
#
# 注意：Stack 模式的 state 是按 label 分的 dict（不是单一 enum），早期 registry 设计错了
# ============================================================
_GROUP_LIVE_TRACKING_FIELDS: List[FieldDef] = [
    _f("live.tracking.cycle_active", "周期活跃中", TYPE_BOOL,
       GROUP_LIVE_TRACKING, "true",
       notes="_tracking_cycle_active — 仅 tracking/counting 模式"),
    _f("live.tracking.container_mode", "容器模式开启", TYPE_BOOL,
       GROUP_LIVE_TRACKING, "false"),

    # ---- Stack 模式（堆叠） ----
    _f("live.tracking.stack_states", "Stack 各标签状态", TYPE_DICT_KV,
       GROUP_LIVE_TRACKING,
       "{good: 'visible', ng: 'idle'}",
       notes="_stack_state — 状态: idle/visible/disappeared"),
    _f("live.tracking.stack_counters", "Stack 各标签累计层数", TYPE_DICT_KV,
       GROUP_LIVE_TRACKING,
       "{good: 4, ng: 1}",
       notes="_stack_counters"),

    # ---- Tracking 模式（物品清点） ----
    _f("live.tracking.active_count", "当前活跃追踪数", TYPE_INT,
       GROUP_LIVE_TRACKING, "6",
       notes="len(_tracking_objects) — 当前帧仍在追踪的目标数"),
    _f("live.tracking.locked_count", "已锁定追踪数", TYPE_INT,
       GROUP_LIVE_TRACKING, "5",
       notes="len(_tracking_locked_ids) — 进入 ROI 已确认的目标数"),
    _f("live.tracking.lost_count", "近期丢失数", TYPE_INT,
       GROUP_LIVE_TRACKING, "1",
       notes="len(_tracking_recently_lost)"),
    _f("live.tracking.prev_count", "上一帧总数", TYPE_INT,
       GROUP_LIVE_TRACKING, "5"),
    _f("live.tracking.was_complete", "周期内曾达成完整", TYPE_BOOL,
       GROUP_LIVE_TRACKING, "false",
       notes="sticky flag — 周期内任意瞬间满足过预期数量"),
    _f("live.tracking.class_counters", "各类追踪计数", TYPE_DICT_KV,
       GROUP_LIVE_TRACKING, "{battery: 4, label: 4}",
       notes="_tracking_class_counters"),
    _f("live.tracking.tracked_objects", "追踪对象详情", TYPE_DICT_KV,
       GROUP_LIVE_TRACKING,
       "{1: {class, bbox, display_id}, ...}",
       notes="拖出来后建议用 {% for tid, obj in live.tracking.tracked_objects.items() %}"),
    _f("live.tracking.item_checklist", "Checklist 完成情况", TYPE_DICT_KV,
       GROUP_LIVE_TRACKING, "{ItemA: 1, ItemB: 0}",
       notes="_tracking_item_checklist"),

    # ---- Event Counter 模式（动作计数） ----
    _f("live.tracking.event_counters", "动作计数", TYPE_DICT_KV,
       GROUP_LIVE_TRACKING, "{push: 12, pull: 3}",
       notes="_event_counters"),
    _f("live.tracking.event_states", "动作当前状态", TYPE_DICT_KV,
       GROUP_LIVE_TRACKING, "{push: 'visible'}",
       notes="_event_state"),

    # ---- Container 模式（box + items） ----
    _f("live.tracking.box_counter", "容器累计编号", TYPE_INT,
       GROUP_LIVE_TRACKING, "12",
       notes="_box_counter — 累计出现的 box 数"),
    _f("live.tracking.boxes", "当前 box 状态", TYPE_DICT_KV,
       GROUP_LIVE_TRACKING,
       "{display_id: {bbox, is_complete, item_counts}, ...}"),
    _f("live.tracking.settled_boxes", "已结算 box 总数", TYPE_INT,
       GROUP_LIVE_TRACKING, "10"),
    _f("live.tracking.settled_ok", "结算 OK 数", TYPE_INT,
       GROUP_LIVE_TRACKING, "9"),
    _f("live.tracking.settled_ng", "结算 NG 数", TYPE_INT,
       GROUP_LIVE_TRACKING, "1"),

    # ---- Scan-D 模式（容器跨线触发扫码） ----
    _f("live.tracking.scan_d_armed_box", "等扫码的 box id", TYPE_INT,
       GROUP_LIVE_TRACKING, "23",
       notes="_scan_d_armed_box — 当前正等待扫 SN 的 box display_id (None=没有)"),
]


# ============================================================
# counters.{name}  （动态 KV — name 是项目里的计数器名）
# ============================================================
_GROUP_COUNTERS_FIELDS: List[FieldDef] = [
    _f("counters.{name}", "计数器(按名访问)", TYPE_DICT_KV, GROUP_COUNTERS,
       "{{ counters['ng_count'] }}",
       "key 是项目 counters_config 里定义的 name; 用 dict 语法访问避免命名冲突"),
    _f("counters._all", "所有计数器(原 dict)", TYPE_DICT, GROUP_COUNTERS,
       "{ng_count: 12, good_count: 856, ...}",
       "{% for k,v in counters._all.items() %}{{k}}={{v}}{% endfor %}"),
    _f("counters._keys", "计数器名列表", TYPE_LIST, GROUP_COUNTERS,
       "['ng_count', 'good_count']"),
    _f("counters.cycle_total", "周期总数", TYPE_INT, GROUP_COUNTERS, "856"),
    _f("counters.cycle_good", "良品总数", TYPE_INT, GROUP_COUNTERS, "823"),
    _f("counters.cycle_ng", "NG 总数", TYPE_INT, GROUP_COUNTERS, "33"),
]


# ============================================================
# counters_daily.{name}  （计数器按日增量 — 短信日报/日维度报表用）
# ============================================================
_GROUP_COUNTERS_DAILY_FIELDS: List[FieldDef] = [
    _f("counters_daily.{name}", "计数器当日增量(按名访问)", TYPE_DICT_KV, GROUP_COUNTERS_DAILY,
       "{{ counters_daily['合格总数'] }}",
       "来源 counter_daily_stats 台账; 只累计正向增量, 清零/重置不扣减",
       sources=["range"]),
    _f("counters_daily._all", "所有计数器当日增量(dict)", TYPE_DICT, GROUP_COUNTERS_DAILY,
       "{合格总数: 120, NG步骤: 3}", sources=["range"]),
    _f("counters_daily._keys", "当日有增量的计数器名列表", TYPE_LIST, GROUP_COUNTERS_DAILY,
       "['合格总数', 'NG步骤']", sources=["range"]),
]


# ============================================================
# mes.* （cycle_end 上下文里的 MES 子树）
# ============================================================
_GROUP_MES_FIELDS: List[FieldDef] = [
    _f("mes.workpiece", "工件子对象", TYPE_DICT, GROUP_MES, "(同 workpiece.*)",
       "等同 workpiece.*; 历史兼容字段"),
    _f("mes.order", "工单子对象", TYPE_DICT, GROUP_MES, "(同 order.*)"),
    _f("mes.scanner", "扫码器子对象", TYPE_DICT, GROUP_MES, "(同 scanner.*)"),
    _f("mes.barcode", "条码字符串", TYPE_STR, GROUP_MES, "8SSC21K6..."),
    _f("mes.box_serial", "所属箱条码", TYPE_STR, GROUP_MES, "BOX-20260503-A-001"),
    _f("mes.station_id", "上报站点(集群模式)", TYPE_STR, GROUP_MES, "A"),
    _f("mes.is_box_complete", "整箱完成(集群)", TYPE_BOOL, GROUP_MES, "false"),
    _f("mes.gateway_pushed", "已推送 MES Gateway", TYPE_BOOL, GROUP_MES, "true"),
    _f("mes.gateway_response", "Gateway 响应原文", TYPE_STR, GROUP_MES, "{ok:1}"),
    _f("mes.external_devices", "外设最新值(JSON)", TYPE_JSON, GROUP_MES,
       "{weight: 256.4, ...}"),
]


# ============================================================
# stats.* （单 Session 聚合，仅批量导出场景有）
# ============================================================
_GROUP_STATS_FIELDS: List[FieldDef] = [
    _f("stats.total_cycles", "总周期数", TYPE_INT, GROUP_STATS, "856",
       sources=["range"]),
    _f("stats.good_cycles", "良品周期数", TYPE_INT, GROUP_STATS, "823"),
    _f("stats.ng_cycles", "NG 周期数", TYPE_INT, GROUP_STATS, "33"),
    _f("stats.yield_rate", "良率(%)", TYPE_FLOAT, GROUP_STATS, "96.14"),
    _f("stats.avg_cycle_time", "平均周期时长(秒)", TYPE_FLOAT, GROUP_STATS, "12.45"),
    _f("stats.min_cycle_time", "最短周期时长(秒)", TYPE_FLOAT, GROUP_STATS, "8.21"),
    _f("stats.max_cycle_time", "最长周期时长(秒)", TYPE_FLOAT, GROUP_STATS, "18.93"),
    _f("stats.avg_cycle_interval", "平均周期间隔(秒)", TYPE_FLOAT, GROUP_STATS, "3.12"),
    _f("stats.step_averages", "各步骤平均时长", TYPE_LIST, GROUP_STATS,
       "[{label, avg_duration, ...}]",
       "用 {% for s in stats.step_averages %}...{% endfor %}"),
    _f("stats.step_averages[*].label", "步骤名", TYPE_STR, GROUP_STATS, "焊接检测"),
    _f("stats.step_averages[*].count", "出现次数", TYPE_INT, GROUP_STATS, "856"),
    _f("stats.step_averages[*].good_count", "通过次数", TYPE_INT, GROUP_STATS, "850"),
    _f("stats.step_averages[*].ng_count", "失败次数", TYPE_INT, GROUP_STATS, "6"),
    _f("stats.step_averages[*].avg_duration", "平均时长(秒)", TYPE_FLOAT,
       GROUP_STATS, "2.45"),
    _f("stats.step_averages[*].min_duration", "最短时长(秒)", TYPE_FLOAT,
       GROUP_STATS, "1.20"),
    _f("stats.step_averages[*].max_duration", "最长时长(秒)", TYPE_FLOAT,
       GROUP_STATS, "4.80"),
    _f("stats.step_averages[*].avg_interval", "平均间隔(秒)", TYPE_FLOAT,
       GROUP_STATS, "0.50"),
    _f("stats.step_averages[*].avg_confidence", "平均置信度", TYPE_FLOAT,
       GROUP_STATS, "0.823",
       notes="Step 1.6 后端新增聚合"),
    _f("stats.step_averages[*].max_confidence", "最高置信度", TYPE_FLOAT,
       GROUP_STATS, "0.987"),
    _f("stats.step_averages[*].min_confidence", "最低置信度", TYPE_FLOAT,
       GROUP_STATS, "0.512"),
    _f("stats.ng_distribution", "NG 步骤分布", TYPE_DICT_KV, GROUP_STATS,
       "{焊接: 12, 外观: 8, ...}"),
    _f("stats.events_count", "事件触发统计", TYPE_DICT_KV, GROUP_STATS,
       "{auto_ok: 850, manual_ng: 5}"),
    _f("stats.cycles", "周期列表(完整)", TYPE_LIST, GROUP_STATS,
       "[cycle1, cycle2, ...]",
       "范围导出时可用，每项同 cycle.*  ；批量较大时谨慎"),
]


# ============================================================
# aggregations.* （多 session 跨日聚合）
# ============================================================
_GROUP_AGGREGATIONS_FIELDS: List[FieldDef] = [
    _f("aggregations.date_range", "日期范围", TYPE_STR, GROUP_AGGREGATIONS,
       "2026-05-01 ~ 2026-05-03",
       sources=["range"]),
    _f("aggregations.total_sessions", "session 总数", TYPE_INT, GROUP_AGGREGATIONS, "5"),
    _f("aggregations.total_cycles", "总周期数", TYPE_INT, GROUP_AGGREGATIONS, "4280"),
    _f("aggregations.total_good", "总良品", TYPE_INT, GROUP_AGGREGATIONS, "4115"),
    _f("aggregations.total_ng", "总 NG", TYPE_INT, GROUP_AGGREGATIONS, "165"),
    _f("aggregations.yield_rate", "整体良率(%)", TYPE_FLOAT, GROUP_AGGREGATIONS, "96.15"),
    _f("aggregations.daily_stats", "按日统计", TYPE_LIST, GROUP_AGGREGATIONS,
       "[{date, total, good, ng, yield_rate}, ...]"),
    _f("aggregations.daily_stats[*].date", "日期", TYPE_STR, GROUP_AGGREGATIONS,
       "2026-05-01"),
    _f("aggregations.daily_stats[*].total", "当日周期数", TYPE_INT, GROUP_AGGREGATIONS,
       "856"),
    _f("aggregations.daily_stats[*].good", "当日良品", TYPE_INT, GROUP_AGGREGATIONS,
       "823"),
    _f("aggregations.daily_stats[*].ng", "当日 NG", TYPE_INT, GROUP_AGGREGATIONS, "33"),
    _f("aggregations.daily_stats[*].yield_rate", "当日良率(%)", TYPE_FLOAT,
       GROUP_AGGREGATIONS, "96.14"),
    _f("aggregations.sessions", "Session 列表", TYPE_LIST, GROUP_AGGREGATIONS,
       "[session1, ...]", "每项同 session.*"),
]


# ============================================================
# 汇总
# ============================================================

ALL_FIELDS: List[FieldDef] = (
    _GROUP_TIME
    + _GROUP_APP_FIELDS
    + _GROUP_DISPLAY_FIELDS
    + _GROUP_LICENSE_FIELDS
    + _GROUP_SYSTEM_FIELDS
    + _GROUP_PROJECT_FIELDS
    + _GROUP_CHANNEL_FIELDS
    + _GROUP_SESSION_FIELDS
    + _GROUP_CYCLE_FIELDS
    + _GROUP_STEPS_FIELDS
    + _GROUP_WORKPIECE_FIELDS
    + _GROUP_ORDER_FIELDS
    + _GROUP_OPERATOR_FIELDS
    + _GROUP_DEFECTS_FIELDS
    + _GROUP_BOX_FIELDS
    + _GROUP_SCANNER_FIELDS
    + _GROUP_LIVE_FIELDS
    + _GROUP_LIVE_TRACKING_FIELDS
    + _GROUP_COUNTERS_FIELDS
    + _GROUP_COUNTERS_DAILY_FIELDS
    + _GROUP_MES_FIELDS
    + _GROUP_STATS_FIELDS
    + _GROUP_AGGREGATIONS_FIELDS
)


# 路径到 FieldDef 的快速索引（静态字段）
_FIELDS_BY_PATH: Dict[str, FieldDef] = {f.path: f for f in ALL_FIELDS}


# ============================================================
# v3.46 F8: 插件动态字段
#
# 插件通过 PluginRegistry.export_fields.register(...) 把自己的字段挂进来：
#   - path 必须以 "plugin.<customer_code_snake>." 开头（命名空间隔离）
#   - 每个插件配一个 context provider: provider(db, ctx) -> dict
#     返回值挂到 Jinja 上下文 ctx["plugin"][<cc_snake>] 下
#   - 单 active 插件 + 重启生命周期：同 customer_code 重复注册视为整体替换
#   - 插件停用后字段不在（模板引用走 _SilentUndefined 渲染为空，不崩）
# ============================================================

import threading as _threading

_PLUGIN_LOCK = _threading.Lock()
# customer_code -> List[FieldDef]
_PLUGIN_FIELDS: Dict[str, List[FieldDef]] = {}
# customer_code -> provider(db, ctx) -> Dict[str, Any]
_PLUGIN_PROVIDERS: Dict[str, Any] = {}


def plugin_namespace(customer_code: str) -> str:
    """customer_code 转字段命名空间段（连字符转下划线）"""
    return customer_code.replace("-", "_")


def register_plugin_fields(customer_code: str, fields: List[FieldDef],
                           provider: Optional[Any] = None) -> None:
    """注册（或整体替换）某插件的导出字段。

    参数:
        customer_code: 插件 customer_code
        fields:        FieldDef 列表，path 必须以 plugin.<cc_snake>. 开头，
                       group 必须是 GROUP_PLUGIN
        provider:      可选 callable(db, ctx) -> dict，导出上下文构造时调用，
                       返回值挂到 ctx["plugin"][<cc_snake>]；None = 只注册元数据
    """
    expect_prefix = f"plugin.{plugin_namespace(customer_code)}."
    for f in fields:
        if not f.path.startswith(expect_prefix):
            raise ValueError(
                f"插件字段 path={f.path!r} 必须以 {expect_prefix!r} 开头（命名空间隔离）"
            )
        if f.group != GROUP_PLUGIN:
            raise ValueError(f"插件字段 group 必须是 {GROUP_PLUGIN!r}，实际 {f.group!r}")
    with _PLUGIN_LOCK:
        _PLUGIN_FIELDS[customer_code] = list(fields)
        if provider is not None:
            _PLUGIN_PROVIDERS[customer_code] = provider
        else:
            _PLUGIN_PROVIDERS.pop(customer_code, None)


def unregister_plugin_fields(customer_code: str) -> None:
    """移除某插件的全部导出字段与 provider（停用/测试清理用）"""
    with _PLUGIN_LOCK:
        _PLUGIN_FIELDS.pop(customer_code, None)
        _PLUGIN_PROVIDERS.pop(customer_code, None)


def _plugin_fields_flat() -> List[FieldDef]:
    with _PLUGIN_LOCK:
        return [f for fields in _PLUGIN_FIELDS.values() for f in fields]


def plugin_field_paths(customer_code: str) -> List[str]:
    """某插件已注册字段的 path 列表（诊断快照用）"""
    with _PLUGIN_LOCK:
        return [f.path for f in _PLUGIN_FIELDS.get(customer_code, [])]


def collect_plugin_context(db, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """执行所有插件 provider，返回 {cc_snake: {...}}。

    错误隔离：单个 provider 异常只丢弃该插件的值，不影响导出主流程。
    """
    out: Dict[str, Any] = {}
    with _PLUGIN_LOCK:
        providers = dict(_PLUGIN_PROVIDERS)
    for cc, provider in providers.items():
        try:
            values = provider(db, ctx)
            if isinstance(values, dict):
                out[plugin_namespace(cc)] = values
        except Exception:
            import logging
            logging.getLogger("tianjun.plugin").warning(
                "[Plugin][%s] 导出字段 provider 异常（已隔离）", cc, exc_info=True,
            )
    return out


# ============================================================
# 公共 API
# ============================================================

def list_fields() -> List[Dict[str, Any]]:
    """平铺字段列表 — 给 GET /api/v1/export/fields 用（含插件动态字段）"""
    return [asdict(f) for f in ALL_FIELDS] + [asdict(f) for f in _plugin_fields_flat()]


def list_groups() -> List[Dict[str, Any]]:
    """按 group 分组返回 — 前端字段树第一层（含插件动态字段）"""
    all_fields = ALL_FIELDS + _plugin_fields_flat()
    out = []
    for g in GROUPS_DISPLAY_ORDER:
        items = [asdict(f) for f in all_fields if f.group == g]
        if not items:
            continue
        out.append({
            "group": g,
            "label": GROUP_LABELS.get(g, g),
            "count": len(items),
            "fields": items,
        })
    return out


def lookup_field(path: str) -> Optional[FieldDef]:
    """按 path 查询字段定义（含插件动态字段）；未注册返回 None"""
    hit = _FIELDS_BY_PATH.get(path)
    if hit is not None:
        return hit
    for f in _plugin_fields_flat():
        if f.path == path:
            return f
    return None


def field_paths() -> List[str]:
    """所有 path 的有序列表（debug 用，含插件动态字段）"""
    return [f.path for f in ALL_FIELDS] + [f.path for f in _plugin_fields_flat()]


def stats() -> Dict[str, int]:
    """字段总数 / 各 group 计数（含插件动态字段）"""
    by_group: Dict[str, int] = {}
    all_fields = ALL_FIELDS + _plugin_fields_flat()
    for f in all_fields:
        by_group[f.group] = by_group.get(f.group, 0) + 1
    return {"total": len(all_fields), "by_group": by_group}
