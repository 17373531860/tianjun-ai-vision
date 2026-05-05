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
