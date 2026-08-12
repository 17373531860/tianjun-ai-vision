# -*- coding: utf-8 -*-
"""版本化数据库迁移注册表 + runner（2026-07 治理，RFC: docs/rfc/DB迁移版本化治理_设计方案_RFC.md）。

取代 main.py 里只增不减的 migrate_database() 大列表。机制：

- 每个迁移一个模块 mXXXX_*.py，暴露 MIGRATION_ID + apply(engine)，按编号升序应用
- schema_migrations 表记账已应用项，新迁移"记账跳过"，不再每次启动全量探查
- m0000_legacy 特殊：**不看记账、永远幂等跑一遍**（inspector 逐列探查补列），
  保证任意历史版本的老库（v3.1→v3.31 任意起点）第一次装新版都能补齐到基线
- 记账表建不起来（磁盘满/只读）→ 降级为只跑 m0000 幂等路径，打显著日志，不阻断启动
- 某个新迁移失败 → 事务回滚、停止应用后续迁移、打显著日志，保留原库不阻断启动

新增 schema 变更的做法（AGENTS.md 不变量 8）：
1. 改 ORM 模型
2. 在本目录新建 m<下一个编号>_<语义名>.py::apply(engine) 写对应 DDL/数据回填
3. 把模块名追加到下面 _MIGRATION_MODULES（显式注册，不做目录扫描——兼容 Nuitka 编译形态）

WS5(PG) 迁移体系定论（2026-08，双方言口径）：
- **SQLite 与 PostgreSQL 走同一条运行时路径**：main.py create_all（建缺失表）
  + 本 runner apply_pending（补列/索引/数据回填）。不给 PG 单开 alembic 修订流。
- alembic 只承担两件事：① CI 校验 ORM↔schema 一致性（PG 基线迁移 job）
  ② sqlite_to_pg 迁移工具建 schema 的可选入口（后端首启 create_all 是另一条等价路径）。
  alembic versions 不追加增量修订。
- 因此**每个 mXXXX 迁移必须双方言可用**：要么用双方言兼容语法
  （CREATE INDEX IF NOT EXISTS / ADD COLUMN <基本类型>），要么按
  get_dialect() 分支（参考 m0000 的类型翻译、m0002 的 PG 分支）。
  SQLite 专属写法（AUTOINCREMENT、PRAGMA、json_extract）禁止直接出现在迁移里。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import inspect, text

# ==================== 显式注册表（按编号升序） ====================
_MIGRATION_MODULES = [
    "m0000_legacy",
    "m0001_hot_path_indexes",
    "m0002_pkg_completed_order_rescan",
    "m0003_pkg_tail_paper_order_settle",
    "m0004_pkg_box_label_scan",
    "m0005_pkg_sync_work_orders",
    "m0006_sms_report_content_template",
    "m0007_defect_cycle_index",
    "m0008_model_interconnect_meta",
    # SY9 分支原编号 m0006 与主线 sms 迁移撞号, 合入时重编号 m0009
    # (apply 幂等补列, 上银现场老库若已按旧 ID 记账, 重跑也零影响)
    "m0009_pkg_tail_paper_only_after_awaiting",
]

# m0000 不看记账、每次启动都幂等跑（老库任意版本起跳的安全网）
_ALWAYS_RUN = {"m0000_legacy"}


def _load_migrations():
    import importlib
    mods = []
    for name in _MIGRATION_MODULES:
        mod = importlib.import_module(f"{__name__}.{name}")
        assert getattr(mod, "MIGRATION_ID", None) == name, \
            f"迁移模块 {name} 的 MIGRATION_ID 与文件名不一致"
        mods.append(mod)
    return mods


def _ensure_ledger(engine) -> bool:
    """建 schema_migrations 记账表；失败返回 False（降级模式）。"""
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "  migration_id VARCHAR(64) PRIMARY KEY,"
                "  applied_at VARCHAR(32)"
                ")"))
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001 — 与历史迁移同级容错，不阻断启动
        print(f"[DB][迁移] ⚠️ schema_migrations 记账表创建失败, 降级为仅跑幂等基线: {e}")
        return False


def _applied_ids(engine) -> set:
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT migration_id FROM schema_migrations")).fetchall()
        return {r[0] for r in rows}
    except Exception as e:  # noqa: BLE001
        print(f"[DB][迁移] ⚠️ 读取记账失败, 按空记账处理: {e}")
        return set()


def _record(engine, migration_id: str):
    try:
        with engine.connect() as conn:
            conn.execute(
                text("INSERT INTO schema_migrations (migration_id, applied_at) "
                     "VALUES (:mid, :ts)"),
                {"mid": migration_id, "ts": datetime.now().isoformat(timespec="seconds")})
            conn.commit()
    except Exception as e:  # noqa: BLE001 — 记账失败不致命，下次启动幂等/重跑兜底
        print(f"[DB][迁移] ⚠️ 记账 {migration_id} 失败: {e}")


def apply_pending(engine):
    """启动时调用：按序应用缺失迁移。行为对外等价于旧 migrate_database()。"""
    mods = _load_migrations()
    ledger_ok = _ensure_ledger(engine)
    applied = _applied_ids(engine) if ledger_ok else set()

    for mod in mods:
        mid = mod.MIGRATION_ID
        if mid in _ALWAYS_RUN:
            # 幂等基线：永远跑（内部 inspector 逐列探查，无副作用重入）
            mod.apply(engine)
            if ledger_ok and mid not in applied:
                _record(engine, mid)
            continue
        if not ledger_ok:
            print(f"[DB][迁移] ⚠️ 降级模式跳过 {mid}（记账不可用）")
            continue
        if mid in applied:
            continue
        print(f"[DB][迁移] 应用 {mid} ...")
        try:
            mod.apply(engine)
        except Exception as e:  # noqa: BLE001
            print(f"[DB][迁移] ❌ {mid} 失败, 停止应用后续迁移（原库保留）: {e}")
            break
        _record(engine, mid)
        print(f"[DB][迁移] ✅ {mid} 完成")
