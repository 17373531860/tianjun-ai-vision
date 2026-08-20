# -*- coding: utf-8 -*-
"""录像归档规则 (v3.53 一期) — 周期录像自动拷贝到客户指定目录。

设计要点:
- 规则形态照 export_models.ExportRealtimeRule (触发 x 筛选 x 目的地 x 命名),
  但搬运对象是录像文件而不是渲染文本。
- 新表由 Base.metadata.create_all 自建 (本项目规范: 新表不写 mXXXX 迁移),
  需在 backend/main.py 与 alembic/env.py 各补一行 import。
- 台账表 video_archive_logs 记录每个文件的去向/成败, 供 Data 页观测与
  后续"归档即清理"联动 (二期) 使用。
"""
from sqlalchemy import (
    Boolean, Column, DateTime, Integer, String, Text, JSON, func,
)

from backend.db.database import Base


class VideoArchiveRule(Base):
    """录像归档规则 — 周期录像收尾后按规则拷贝到目标目录。

    一期只支持 cycle 录像 (trigger 固定 cycle_end); session/step 归档、
    sidecar 伴随文件、远端 adapter 见二期以后的路线图。
    """
    __tablename__ = "video_archive_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    description = Column(Text, nullable=True)

    # 结果筛选: all | ng_only | ok_only
    result_filter = Column(String(16), nullable=False, default="ng_only")

    # 通道过滤 (JSON list of int, 空/null = 全部通道)
    channel_filter = Column(JSON, nullable=True)

    # 项目过滤 (JSON list of int, 空/null = 全部项目)
    project_filter = Column(JSON, nullable=True)

    # 目标目录 (绝对路径; 黑名单护栏见 services/video_archive.validate_dest_dir)
    dest_dir = Column(String(500), nullable=False)

    # 目的地是否按日期分子目录 (YYYY-MM-DD, 与本地录像目录同构)
    subdir_by_date = Column(Boolean, nullable=False, default=True)

    # 文件名模板 (Jinja2, 上下文与自定义导出同源: 311 字段中央仓库)
    # 注: cycle.result 渲染为「良品/NG」, 文件名默认用显式 OK/NG 更通用
    filename_template = Column(
        String(256), nullable=False,
        default="{{ workpiece.serial_no | default(cycle.id, true) }}"
                "_{{ 'OK' if cycle.is_good else 'NG' }}.mp4")

    # 重名策略: rename (追加序号) | overwrite (覆盖) | skip (跳过)
    overwrite_policy = Column(String(16), nullable=False, default="rename")

    # ---- 二期: 证据能力 ----
    # NG 关键帧快照: 归档时附带 NG 结算瞬间的带框画面 JPEG
    attach_keyframe = Column(Boolean, nullable=False, default=False)
    # 关键帧水印 (时间戳/工位/SN 烧入左上角)
    keyframe_watermark = Column(Boolean, nullable=False, default=True)
    # sidecar 伴随报告: 绑一个自定义导出模板 (export_templates.id),
    # 录像旁成对渲染数据报告 (同 basename 不同扩展名)
    sidecar_template_id = Column(Integer, nullable=True)
    # 证据包: 录像+关键帧+sidecar 打成一个 zip 落位 (而不是散文件)
    bundle_zip = Column(Boolean, nullable=False, default=False)
    # 变换: none (整段) | clip_tail (事件切片: 只留结算前最后 clip_seconds 秒,
    # NG 发生在周期结算瞬间, 尾段即"出事那一下")
    transform = Column(String(16), nullable=False, default="none")
    clip_seconds = Column(Integer, nullable=False, default=10)

    # ---- 四期: 远端目的地与治理 ----
    # 目的地类型: local_dir | ftp | sftp | s3 | http | plugin:<name>
    dest_type = Column(String(32), nullable=False, default="local_dir")
    # adapter 配置 (host/port/user/bucket/url 等; 敏感字段落库前 Fernet 加密,
    # 见 services/archive_secrets.py)
    dest_config = Column(JSON, nullable=True)
    # 归档时间窗 "HH:MM-HH:MM" (如 22:00-06:00 只在夜间搬运; 空 = 全天)
    active_window = Column(String(16), nullable=True)
    # 带宽限速 KB/s (0/空 = 不限)
    bandwidth_limit_kbps = Column(Integer, nullable=True)
    # 归档后删本地源 (校验目的地字节数一致后才删, 且回写 DetectionCycle.video_path=None)
    delete_source_after = Column(Boolean, nullable=False, default=False)

    # 最近一次运行状态
    last_run_time = Column(DateTime(timezone=True), nullable=True)
    last_run_status = Column(String(16), nullable=True)  # success | failed | skipped
    last_run_error = Column(Text, nullable=True)
    last_dest_file = Column(String(500), nullable=True)

    # 累计统计
    success_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(),
                        nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


class VideoArchiveLog(Base):
    """归档台账 — 每次搬运一条 (成功/失败/跳过), 观测与追溯用。"""
    __tablename__ = "video_archive_logs"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, nullable=True, index=True)
    cycle_id = Column(Integer, nullable=True, index=True)
    channel_id = Column(Integer, nullable=True)

    src_path = Column(String(500), nullable=True)
    dest_path = Column(String(500), nullable=True)

    status = Column(String(16), nullable=False)  # success | failed | skipped
    error = Column(Text, nullable=True)

    file_size = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(),
                        nullable=False, index=True)
