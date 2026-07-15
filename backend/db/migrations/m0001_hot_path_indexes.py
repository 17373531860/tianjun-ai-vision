# -*- coding: utf-8 -*-
"""m0001: 热路径索引（2026-07 川南"框冻结"第三批优化）。

补两个长期缺失的查询索引:
- step_records.cycle_id   — 周期收尾/自动清理/数据页都按周期号查步骤, 无索引时
  每次都全表扫描; 清理分批删除 (v3.38) 在大库上每批的锁窗口也被扫描时长放大。
- video_clips.related_id  — 清理按归属周期/步骤批量找录像记录, 同为全表扫描热点。

CREATE INDEX 在大库上耗秒级 (百万行 ≈ 数秒), 属一次性成本;
打印耗时留痕, 老客户升级首启动稍慢属预期。
"""
from __future__ import annotations

import time

from sqlalchemy import text

MIGRATION_ID = "m0001_hot_path_indexes"

_INDEXES = [
    ("ix_step_records_cycle_id", "step_records", "cycle_id"),
    ("ix_video_clips_related_id", "video_clips", "related_id"),
]


def apply(engine):
    with engine.connect() as conn:
        for name, table, column in _INDEXES:
            t0 = time.time()
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))
            conn.commit()
            print(f"[DB][迁移] 索引 {name} 就绪 ({(time.time() - t0) * 1000:.0f}ms)")
