"""
A4 回归测试: 过程日志表(扫码 / MES通讯 / 外设)纳入自动清理。

客户实锤(2026-06): 设了自动删除保留期, 但磁盘没变小、保存路径下还有旧日期数据。
根因之一: 扫码日志 / MES通讯日志 / 外设日志这三张"过程流水"表只增不减,
自动清理从来不碰它们。

本测试锁定的观察点:
  1. 跑一次自动清理后, 超过全局保留期的三张过程日志被删除;
  2. 近期(未过期)的过程日志保留;
  3. 业务表(工单)绝不被这段日志清理逻辑误删;
  4. log_retention_days 单独配置时, 用独立保留期(可比全局更久, 留排查)。

先红后绿: 把 sessions_maintenance._perform_auto_cleanup 里 2c 段注释掉,
本测试第 1/2 条断言会 FAIL(日志行数不变); 恢复后 PASS。
"""
from datetime import datetime, timedelta


def _seed(db, ScanLog, MESCommLog, ExternalDeviceLog, WorkOrder):
    old = datetime.now() - timedelta(days=40)      # 超 30 天全局保留期
    recent = datetime.now() - timedelta(days=1)    # 近期, 应保留
    db.add_all([
        ScanLog(raw_data="OLD-SCAN", created_at=old),
        ScanLog(raw_data="NEW-SCAN", created_at=recent),
        MESCommLog(direction="out", created_at=old),
        MESCommLog(direction="out", created_at=recent),
        ExternalDeviceLog(raw_data="OLD-DEV", created_at=old),
        ExternalDeviceLog(raw_data="NEW-DEV", created_at=recent),
        # 业务表: 过期工单, 不该被"过程日志清理"段触碰
        WorkOrder(order_no="__a4_wo__", product_name="x", created_at=old),
    ])
    db.commit()


def _clear(db, *models):
    for m in models:
        db.query(m).delete(synchronize_session=False)
    db.commit()


def test_a4_process_logs_cleaned_by_global_retention():
    from backend.db.database import SessionLocal
    from backend.models.mes_models import ScanLog, MESCommLog, ExternalDeviceLog, WorkOrder
    from backend.api.sessions_maintenance import _perform_auto_cleanup, _set_system_config

    db = SessionLocal()
    try:
        _clear(db, ScanLog, MESCommLog, ExternalDeviceLog)
        db.query(WorkOrder).filter(WorkOrder.order_no == "__a4_wo__").delete(
            synchronize_session=False)
        db.commit()

        _set_system_config(db, "retention_days", "30", "")
        _set_system_config(db, "auto_cleanup", "true", "")
        _set_system_config(db, "log_retention_days", "0", "")  # 0 = 跟随全局
        db.commit()

        _seed(db, ScanLog, MESCommLog, ExternalDeviceLog, WorkOrder)
        assert db.query(ScanLog).count() == 2
        assert db.query(MESCommLog).count() == 2
        assert db.query(ExternalDeviceLog).count() == 2
    finally:
        db.close()

    _perform_auto_cleanup()

    db = SessionLocal()
    try:
        assert db.query(ScanLog).count() == 1, "过期扫码日志未被清理"
        assert db.query(MESCommLog).count() == 1, "过期MES通讯日志未被清理"
        assert db.query(ExternalDeviceLog).count() == 1, "过期外设日志未被清理"
        assert db.query(ScanLog).first().raw_data == "NEW-SCAN", "误删了近期日志"
        assert db.query(ExternalDeviceLog).first().raw_data == "NEW-DEV"
        # 业务表必须完好
        assert db.query(WorkOrder).filter(WorkOrder.order_no == "__a4_wo__").first() is not None, \
            "过程日志清理误删了业务工单"
    finally:
        db.close()


def test_a4_independent_log_retention_keeps_recent_logs_longer():
    """log_retention_days 设大于全局时, 过程日志用独立(更久)保留期, 不被全局保留期误删。"""
    from backend.db.database import SessionLocal
    from backend.models.mes_models import ScanLog, MESCommLog, ExternalDeviceLog
    from backend.api.sessions_maintenance import _perform_auto_cleanup, _set_system_config

    db = SessionLocal()
    try:
        _clear(db, ScanLog, MESCommLog, ExternalDeviceLog)
        _set_system_config(db, "retention_days", "30", "")
        _set_system_config(db, "auto_cleanup", "true", "")
        _set_system_config(db, "log_retention_days", "90", "")  # 日志单独保留 90 天
        db.commit()

        # 40 天前: 超全局 30, 但未超日志 90 → 应保留
        d40 = datetime.now() - timedelta(days=40)
        # 100 天前: 超日志 90 → 应删
        d100 = datetime.now() - timedelta(days=100)
        db.add_all([
            ScanLog(raw_data="KEEP-40", created_at=d40),
            ScanLog(raw_data="DROP-100", created_at=d100),
        ])
        db.commit()
    finally:
        db.close()

    _perform_auto_cleanup()

    db = SessionLocal()
    try:
        rows = {r.raw_data for r in db.query(ScanLog).all()}
        assert "KEEP-40" in rows, "独立日志保留期(90天)被全局保留期(30天)误删"
        assert "DROP-100" not in rows, "超独立保留期的日志未删"
    finally:
        # 收尾: 复位配置, 清数据, 不污染其他测试
        _clear(db, ScanLog, MESCommLog, ExternalDeviceLog)
        from backend.api.sessions_maintenance import _set_system_config as _ssc
        _ssc(db, "log_retention_days", "0", "")
        db.commit()
        db.close()
