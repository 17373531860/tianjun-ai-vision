"""
每日短信日报数据模型 (v3.46+)

三张表：
- SmsReportRule    短信日报规则（发送时间 / 数据窗口 / 指标勾选 / 手机号 / 变量映射）
- SmsSendLog       发送记录（每次发送一行，成功/失败/重试都留痕）
- CounterDailyStat 自定义计数器按日增量台账（date+project+channel+counter_name 唯一）

设计原则：
1. 国内云短信是"签名 + 审核模板 + 变量"，不是自由文本 —— 规则存的是
   "勾选字段路径 → 云模板变量名"的映射，发送时渲染变量 dict 交给通道;
   内容式通道 (AT 短信猫等) 则用 content_template 在本端渲染正文
2. 计数器按日增量只累计正向增加（清零/重置不扣减），热路径不逐次写 DB，
   由内存日桶节流落库（见 services/counter_daily.py）
3. 发送通道统一走 services/sms_providers（与 NG 短信通知共用），
   通道选择与凭据存共享 sms_config.json，不进本表
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, JSON,
    Date, Index, UniqueConstraint
)
from sqlalchemy.sql import func
from backend.db.database import Base


# ============================================================
# 短信日报规则
# ============================================================

class SmsReportRule(Base):
    """短信日报规则 — 按 cron 定时把当日/昨日数据摘要发到手机

    与 ExportScheduledRule 的区别:
      - scheduled export: 聚合明细写本地文件
      - sms report: 聚合 KPI 摘要 → 云短信模板变量 → 发手机
    """
    __tablename__ = "sms_report_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    description = Column(Text, nullable=True)

    # ---- 触发时机 ----
    # 5 字段标准 cron; 前端 UI 提供"每天 HH:MM"简化模式自动转 cron
    cron_expression = Column(String(64), nullable=False, default="0 20 * * *")

    # ---- 数据窗口 ----
    # today     今天 0 点 → 发送时刻（"发今天数据"主场景, 建议晚上发）
    # yesterday 昨天全天（凌晨发昨天日报场景）
    data_window_type = Column(String(16), nullable=False, default="today")

    # ---- 范围 ----
    # 汇总模式(False): 全部/选中工位合并成一条短信
    # 分工位模式(True): 每个工位单独一条短信（避开云短信变量长度限制）
    group_by_channel = Column(Boolean, nullable=False, default=False)
    # 工位过滤; None/[] = 全部工位
    channel_ids = Column(JSON, nullable=True)
    # 项目过滤; None = 全部项目
    project_id = Column(Integer, nullable=True, index=True)

    # ---- 内容 ----
    # 勾选的指标字段路径列表, 来自字段中央仓库
    # 例: ["stats.total_cycles", "stats.good_cycles", "stats.yield_rate", "counters_daily.合格总数"]
    metrics = Column(JSON, nullable=True)

    # 字段路径 → 云短信模板变量名 的映射
    # 例: {"stats.total_cycles": "total", "stats.yield_rate": "rate"}
    # 云平台审核过的模板里用 ${total} ${rate} 引用
    template_param_mapping = Column(JSON, nullable=True)

    # 固定注入的额外变量 (如 {"line": "3号包装线"}), 可空
    extra_params = Column(JSON, nullable=True)

    # 收件手机号列表 ["13800000000", ...]
    phone_numbers = Column(JSON, nullable=True)

    # 云模板 code/ID 覆盖 (可空; 空则用共享短信配置里对应云通道的默认模板)
    template_code = Column(String(64), nullable=True)

    # 短信正文模板 (可空) — 内容式通道 (AT 短信猫/通用 HTTP/WxPusher) 用,
    # ${变量名} 占位符取自变量映射; 空则退化为 "变量:值" 拼接。
    # 云模板通道 (aliyun/tencent) 忽略此字段, 正文由云平台审核模板渲染。
    content_template = Column(Text, nullable=True)

    # ---- 运行状态 ----
    last_run_time = Column(DateTime(timezone=True), nullable=True)
    last_run_status = Column(String(16), nullable=True)  # success | partial | failed | skipped
    last_run_error = Column(Text, nullable=True)
    next_run_time = Column(DateTime(timezone=True), nullable=True, index=True)

    success_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


Index("ix_sms_report_rules_enabled_nextrun",
      SmsReportRule.enabled, SmsReportRule.next_run_time)


# ============================================================
# 发送记录
# ============================================================

class SmsSendLog(Base):
    """短信发送记录 — 一次适配器调用一行（分工位模式每工位一行）"""
    __tablename__ = "sms_send_logs"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, nullable=True, index=True)  # 规则可能被删, 不做硬 FK
    rule_name = Column(String(128), nullable=True)

    triggered_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # cron 定时 | manual_test 试发
    source_type = Column(String(16), nullable=False, default="cron")

    provider = Column(String(32), nullable=True)          # aliyun | tencent | mock
    channel_id = Column(Integer, nullable=True)           # 分工位模式时的工位; 汇总为 None
    phone_numbers = Column(JSON, nullable=True)
    template_code = Column(String(64), nullable=True)
    template_params = Column(JSON, nullable=True)         # 实际发出的变量 dict

    success = Column(Boolean, nullable=False, default=False)
    retry_count = Column(Integer, nullable=False, default=0)
    error_msg = Column(Text, nullable=True)
    provider_response = Column(JSON, nullable=True)       # 服务商原始响应摘要(排查用)


Index("ix_sms_send_logs_rule_time", SmsSendLog.rule_id, SmsSendLog.triggered_at.desc())


# ============================================================
# 计数器按日增量台账
# ============================================================

class CounterDailyStat(Base):
    """自定义计数器按日增量 — 只累计正向增加, 清零/重置不扣减

    写入方: services/counter_daily.py 的内存日桶节流 flush
    读取方: 短信日报聚合 (counters_daily.{name} 字段) / 未来报表
    """
    __tablename__ = "counter_daily_stats"

    id = Column(Integer, primary_key=True, index=True)
    stat_date = Column(Date, nullable=False, index=True)
    project_id = Column(Integer, nullable=False, default=0)   # 0 = 无项目上下文
    channel_id = Column(Integer, nullable=False, default=0)
    counter_name = Column(String(128), nullable=False)

    # 当日累计正向增量
    delta = Column(Integer, nullable=False, default=0)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("stat_date", "project_id", "channel_id", "counter_name",
                         name="uq_counter_daily_key"),
    )
