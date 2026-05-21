"""
自定义导出系统数据模型 (v3.5.0+)

三张表：
- ExportTemplate     模板中央仓库（系统预设 + 用户自建，所有消费者共用）
- ExportRealtimeRule 实时导出规则（每个 cycle/session/box 完成时自动写文件）
- ExportRunLog       运行日志（实时和批量导出都记，便于排查）

设计原则：
1. 模板格式无关 + 内容用 Jinja2 写
2. 实时规则附带"输入文件改写"三种模式 (none / read_template / append)
3. 系统预设模板 is_system=True 不可删（可复制）
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, JSON,
    ForeignKey, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.db.database import Base


# ============================================================
# 模板中央仓库
# ============================================================

class ExportTemplate(Base):
    """导出模板 — 所有消费者共用同一份模板定义

    消费者：
    - Data 页"自定义导出"对话框（立即下载）
    - Data 页"实时输出"开关（每 cycle 自动写文件）
    - Data 页 4 个一键按钮（导出当日/周/月/范围）— 各绑一份系统预设
    - 未来可能：MES Gateway 的 file_text 适配器复用
    """
    __tablename__ = "export_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False, index=True)
    description = Column(Text, nullable=True)

    # 输出格式: txt | csv | docx | xlsx | pdf
    format = Column(String(16), nullable=False, default="txt", index=True)

    # 模板内容 (Jinja2 源码)
    # - txt/csv: 整段文本含 {{ }} 占位符
    # - docx 路线 A (自动样式): 同 txt，渲染时按统一样式生成 docx
    # - docx 路线 B (上传占位符): content 留空，用 docx_template_path
    # - xlsx/pdf: 同上规则
    content = Column(Text, nullable=False, default="")

    # 路线 B 用：用户上传的 .docx/.xlsx 占位符模板文件路径（DATA_DIR 下相对路径）
    template_file_path = Column(String(500), nullable=True)

    # 适用场景: batch | realtime | both
    # 注意 (v3.5.0)：用户决定不强制限制，all 模板都能用在所有场景
    # 该字段保留作为"建议提示"用，UI 上检测到 {% for %} 时给提示
    scope = Column(String(16), nullable=False, default="both")

    # 系统预设 (4 个一键按钮 + 客户 SN.txt 等内置模板) — 不可删，可复制
    is_system = Column(Boolean, nullable=False, default=False, index=True)

    # 系统预设的内置 ID（如 "builtin_session_csv"），用户模板为 NULL
    # 用于代码侧引用（如 4 个按钮根据 builtin_id 找对应模板）
    builtin_id = Column(String(64), nullable=True, unique=True, index=True)

    # v3.7.2 模板自带"推荐规则配置".
    # 用法: 选中此模板新建 ExportRealtimeRule 时, 前端自动用此字典填空字段
    # (input_dir / output_dir 这些场地相关的不在里面, 客户自己填).
    # 适用例: 扫码器旁路预设带上
    #   {"trigger_event": "cycle_end", "input_file_mode": "none",
    #    "filename_template": "{{ latest_input_filename() }}",
    #    "newline": "crlf", "latest_file_strategy": "cycle_start_snapshot",
    #    "latest_file_wait_stable_ms": 100, "latest_file_max_age_sec": 60,
    #    ...}
    # 客户复制模板成自建模板时, 这份 JSON 跟着拷过去 (default_rule_config
    # 不绑 builtin_id, 只是模板的一部分属性).
    default_rule_config = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


# ============================================================
# 实时导出规则
# ============================================================

class ExportRealtimeRule(Base):
    """实时导出规则 — 每个 cycle/session/box 完成时自动渲染模板写文件

    类似 MES Gateway 的连接列表，但目的是写本地/网络文件而不是 HTTP 推送。
    可以建多条规则，每条独立的 enabled / channel_filter / project_filter。
    """
    __tablename__ = "export_realtime_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    description = Column(Text, nullable=True)

    template_id = Column(Integer, ForeignKey("export_templates.id", ondelete="RESTRICT"),
                         nullable=False, index=True)

    # 输出目录（绝对路径或相对 DATA_DIR）
    output_dir = Column(String(500), nullable=False)

    # 文件名模板（Jinja2，渲染时与 context 同源）
    # 默认 "{{ workpiece.serial_no | default(cycle.id) }}.{{ template.format }}"
    filename_template = Column(String(256), nullable=False, default="{{ cycle.id }}.txt")

    # 输入文件改写模式：
    #   none           : 不读输入文件，直接写输出（最常见）
    #   read_template  : 读输入文件作为模板内容（覆盖 ExportTemplate.content）
    #   append         : 把渲染结果追加到输入文件末尾
    input_file_mode = Column(String(16), nullable=False, default="none")

    # 输入文件目录（input_file_mode != none 时必填）
    # 实际文件名 = "{input_dir}/{filename_template 渲染结果}"
    input_dir = Column(String(500), nullable=True)

    # 触发事件: cycle_end | session_end | box_complete
    trigger_event = Column(String(32), nullable=False, default="cycle_end", index=True)

    # 通道过滤 (JSON list of int, 空/null = 全部通道)
    channel_filter = Column(JSON, nullable=True)

    # 项目过滤 (JSON list of int, 空/null = 全部项目)
    project_filter = Column(JSON, nullable=True)

    # 写入冲突策略: overwrite (覆盖) | rename (加时间戳后缀) | skip (跳过)
    overwrite_policy = Column(String(16), nullable=False, default="overwrite")

    # 文件编码: utf-8 | utf-8-sig (带 BOM, Windows Excel 友好) | gbk | ascii
    encoding = Column(String(16), nullable=False, default="utf-8")

    # 换行符: lf | crlf
    newline = Column(String(8), nullable=False, default="lf")

    # v3.7.2 扫码器旁路 — 取「输入目录最新文件」的策略:
    #   mtime                 : 每次触发现去翻文件夹按 mtime 排序 (最简单, 最不稳)
    #   mtime_stable          : 同 mtime 但加 wait_stable_ms / max_age_sec 保险
    #   cycle_start_snapshot  : 周期开始那一刻就锁定快照写到 DetectionCycle.external_meta,
    #                           cycle_end 直接读快照 (最稳, 推荐, 默认)
    latest_file_strategy = Column(String(32), nullable=False,
                                  default="cycle_start_snapshot")

    # mtime_stable 策略参数:
    #   wait_stable_ms: 读两次中间等待毫秒数, 两次 size+mtime 一致才认为稳定. 0 = 不等
    #   max_age_sec:    只接受 mtime 在最近 N 秒内的文件. 0 = 不限制
    latest_file_wait_stable_ms = Column(Integer, nullable=False, default=100)
    latest_file_max_age_sec = Column(Integer, nullable=False, default=0)

    # v3.7.2 同名去重 — 防止两个周期撞同一个输入文件名导致输出覆盖.
    #   dedupe_same_filename:    开关, 默认 false
    #   dedupe_retry_max_sec:    重试最大等待秒数 (默认 5)
    #   dedupe_retry_interval_ms: 轮询间隔毫秒 (默认 100)
    #   last_used_input_filename: 上一次成功用过的输入文件名 (运行时回填)
    #   超时后 → 按 Q2 选择 b: 跳过本规则 (写 SKIPPED 日志, 不写文件)
    dedupe_same_filename = Column(Boolean, nullable=False, default=False)
    dedupe_retry_max_sec = Column(Integer, nullable=False, default=5)
    dedupe_retry_interval_ms = Column(Integer, nullable=False, default=100)
    last_used_input_filename = Column(String(256), nullable=True)

    # 最近一次运行状态
    last_run_time = Column(DateTime(timezone=True), nullable=True)
    last_run_status = Column(String(16), nullable=True)  # success | failed | skipped
    last_run_error = Column(Text, nullable=True)
    last_output_file = Column(String(500), nullable=True)

    # 累计统计
    success_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    template = relationship("ExportTemplate", foreign_keys=[template_id])


# ============================================================
# 运行日志
# ============================================================

class ExportRunLog(Base):
    """导出运行日志 — 实时规则和批量导出都记一行

    便于：
    1. 客户问"为什么这个 SN 没生成文件" — 查 cycle_id 对应日志
    2. 排查模板渲染错误（error_msg 含 traceback）
    3. 统计实时规则成功率
    """
    __tablename__ = "export_run_logs"

    id = Column(Integer, primary_key=True, index=True)

    # 实时规则触发的写 rule_id；批量导出（用户手动点击）写 NULL
    rule_id = Column(Integer, ForeignKey("export_realtime_rules.id", ondelete="SET NULL"),
                     nullable=True, index=True)

    template_id = Column(Integer, ForeignKey("export_templates.id", ondelete="SET NULL"),
                         nullable=True, index=True)

    # 触发场景: realtime | batch | manual_test
    source_type = Column(String(16), nullable=False, default="realtime", index=True)

    # 关联实体（实时模式下其中一个非空，批量为空）
    cycle_id = Column(Integer, nullable=True, index=True)
    session_id = Column(Integer, nullable=True, index=True)
    box_serial = Column(String(128), nullable=True, index=True)

    triggered_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    # 状态: success | failed | skipped
    status = Column(String(16), nullable=False, default="success", index=True)

    output_file = Column(String(500), nullable=True)
    file_size = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    error_msg = Column(Text, nullable=True)

    # 跳过原因: filter_mismatch | overwrite_skip | template_disabled | ...
    skip_reason = Column(String(64), nullable=True)


# 复合索引：rule_id + triggered_at 用于"查某条规则最近 N 条日志"
Index("ix_export_run_logs_rule_time", ExportRunLog.rule_id, ExportRunLog.triggered_at.desc())


# ============================================================
# v3.8.x 定时导出规则
# ============================================================

class ExportScheduledRule(Base):
    """定时导出规则 — 按 cron 表达式周期性自动写一份数据文件

    场景: 客户希望"每天凌晨 0 点把昨天一整天的检测数据导出到 D:\\日报\\"。
    与 ExportRealtimeRule 的区别:
      - realtime: 事件触发 (cycle_end / session_end / box_complete), 单个周期/会话
      - scheduled: 时间触发 (cron), 一段时间范围的数据聚合

    数据窗口语义复用现有 sessions_export 的早晚班 / 跨日截断逻辑:
      - data_window_type 选什么, 调度服务就按对应规则算出 (date, start_date, end_date, start_hour, end_hour)
      - 传给 sessions_export 内部 builder
      - 跨日班次 (start_hour > end_hour) 自动走跨日 OR 查询路径, 跟手动按钮的行为完全一致
    """
    __tablename__ = "export_scheduled_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    description = Column(Text, nullable=True)

    # ---- 触发时机 ----
    # CRON 表达式 (5 字段标准 cron, 例 "0 0 * * *" 表示每天 0:00)
    # 前端 UI 提供"每天几点"简化模式 → 自动转 cron, 也允许直接填高级 cron
    cron_expression = Column(String(64), nullable=False, default="0 0 * * *")

    # ---- 数据窗口 ----
    # 数据窗口类型:
    #   yesterday              昨天全天 (复用按日导出, date=昨天)
    #   today                  今天截至当前 (date=今天)
    #   last_n_hours           过去 N 小时滚动窗 (用 start_date/end_date 精确)
    #   last_n_days            过去 N 天 (用 start_date/end_date)
    #   shift_day_yesterday    昨天白班 (date=昨天, start_hour/end_hour 由 window_config 配)
    #   shift_night_yesterday  昨夜班 (跨日, date=昨天, start_hour > end_hour 自动走跨日逻辑)
    #   custom_offset          自定义日期偏移 + 任意时段
    data_window_type = Column(String(32), nullable=False, default="yesterday")

    # 数据窗口附加参数 (JSON):
    #   last_n_hours: {"n": 24}
    #   last_n_days:  {"n": 7}
    #   shift_*:      {"start_hour": "08:00", "end_hour": "20:00"}
    #   custom_offset: {"date_offset_days": -1, "start_hour": "08:00", "end_hour": "20:00"}
    data_window_config = Column(JSON, nullable=True)

    # 项目过滤 / 工位过滤 (单值, 复用 sessions_export 的入参约定; None = 全部)
    project_id = Column(Integer, nullable=True, index=True)
    channel_id = Column(Integer, nullable=True, index=True)

    # ---- 输出 ----
    # 输出格式: csv | txt | xlsx | docx | pdf
    output_format = Column(String(16), nullable=False, default="csv")

    # 输出目录 (Optional; 为空走 SystemConfig 的全局默认目录 export.scheduled.default_dir)
    output_dir = Column(String(500), nullable=True)

    # 文件名模板 (Jinja2-like 简单占位, 渲染时支持 {date} {datetime} {rule_name} {format} {window_start} {window_end})
    # 默认: "{rule_name}_{date}.{format}"
    filename_template = Column(String(256), nullable=False, default="{rule_name}_{date}.{format}")

    # 模板选择:
    #   use_standard_daily_report=True  → 走 sessions_export 的标准列 (跟手动按钮同一份代码, 无需 template_id)
    #   use_standard_daily_report=False → 走 template_id 指向的 ExportTemplate (自定义 Jinja2 模板)
    use_standard_daily_report = Column(Boolean, nullable=False, default=True)
    template_id = Column(Integer, ForeignKey("export_templates.id", ondelete="SET NULL"),
                         nullable=True, index=True)

    # 文件编码 / 换行 / 覆盖策略 (复用 ExportRealtimeRule 同名字段语义)
    encoding = Column(String(16), nullable=False, default="utf-8-sig")
    newline = Column(String(8), nullable=False, default="lf")
    overwrite_policy = Column(String(16), nullable=False, default="overwrite")

    # PT / CT 显示模式 (跟数据页 4 个按钮一致, 影响 CSV 列内容)
    pt_mode = Column(String(16), nullable=True)  # avg | last | None (走系统默认)
    ct_mode = Column(String(16), nullable=True)

    # ---- 运行状态 ----
    last_run_time = Column(DateTime(timezone=True), nullable=True)
    last_run_status = Column(String(16), nullable=True)  # success | failed | skipped | no_data
    last_run_error = Column(Text, nullable=True)
    last_output_file = Column(String(500), nullable=True)

    next_run_time = Column(DateTime(timezone=True), nullable=True, index=True)  # croniter 预算的下次触发时间

    success_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    template = relationship("ExportTemplate", foreign_keys=[template_id])


# 复合索引: enabled + next_run_time 加速调度服务"找到下一个该跑的规则"
Index("ix_export_scheduled_enabled_nextrun",
      ExportScheduledRule.enabled, ExportScheduledRule.next_run_time)
